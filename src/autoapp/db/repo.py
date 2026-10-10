"""Repository functions over the SQLite connection (no ORM)."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

from autoapp.db.status import Status
from autoapp.llm.base import Usage
from autoapp.sources.alerts.models import AlertSource

JOB_FIELDS = frozenset(
    {
        "title", "company", "location", "work_mode", "working_time", "contract_type",
        "job_url", "canonical_url", "application_url", "description", "posted_at",
        "ad_salary_min", "ad_salary_max", "salary_estimate_min", "salary_estimate_max",
        "fingerprint", "group_key", "search_profile", "notes",
    }
)  # fmt: skip

APPLICATION_FIELDS = frozenset(
    {
        "folder", "channel", "contact_person", "contact_email", "reference",
        "salary_expectation", "assumed_skills", "language", "prepared_at", "applied_at",
        "form_entry_url", "form_reached_url", "form_status", "form_checked_at", "form_note",
    }
)  # fmt: skip


def now_iso(now: datetime | None = None) -> str:
    """Timezone-aware UTC timestamp as an ISO string (second precision, sortable)."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


class Repo:
    """Job, source, evaluation, application and status-history operations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- jobs -------------------------------------------------------------

    def _new_id(self) -> str:
        while True:
            candidate = secrets.token_hex(3)  # 6 lowercase hex chars
            if self.get_job(candidate) is None:
                return candidate

    def insert_job(self, *, status: Status | str = Status.NEW, **fields: Any) -> str:
        """Insert a job and return its short id. ``title`` and ``company`` are required.

        No status event is written for the initial status; history records changes.
        """
        unknown = set(fields) - JOB_FIELDS
        if unknown:
            raise TypeError(f"unknown job fields: {sorted(unknown)}")
        if not fields.get("title") or not fields.get("company"):
            raise ValueError("title and company are required")
        st = Status.parse(status)
        job_id = self._new_id()
        stamp = now_iso()
        columns = ["id", "status", "discovered_at", "updated_at", *fields]
        values = [job_id, st.value, stamp, stamp, *fields.values()]
        marks = ", ".join("?" for _ in columns)
        with self.conn:
            self.conn.execute(f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({marks})", values)
        return job_id

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def update_job(self, job_id: str, **fields: Any) -> None:
        """Update plain job columns (not status; use ``transition``)."""
        unknown = set(fields) - JOB_FIELDS
        if unknown:
            raise TypeError(f"unknown job fields: {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?",
                [*fields.values(), now_iso(), job_id],
            )
        if cur.rowcount == 0:
            raise KeyError(job_id)

    def find_by_source(self, source: str, source_job_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT j.* FROM jobs j JOIN job_sources s ON s.job_id = j.id "
            "WHERE s.source = ? AND s.source_job_id = ?",
            (source, source_job_id),
        ).fetchone()

    def find_by_canonical_url(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE canonical_url = ? ORDER BY discovered_at LIMIT 1", (url,)
        ).fetchone()

    def find_by_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE fingerprint = ? ORDER BY discovered_at LIMIT 1", (fingerprint,)
        ).fetchone()

    def find_by_group_key(
        self, group_key: str, within_days: int = 30, now: datetime | None = None
    ) -> sqlite3.Row | None:
        """Find a same-company, same-title job discovered within ``within_days``."""
        since = now_iso((now or datetime.now(UTC)) - timedelta(days=within_days))
        return self.conn.execute(
            "SELECT * FROM jobs WHERE group_key = ? AND discovered_at >= ? "
            "ORDER BY discovered_at LIMIT 1",
            (group_key, since),
        ).fetchone()

    def find_all_by_fingerprint(self, fingerprint: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE fingerprint = ? ORDER BY discovered_at, id", (fingerprint,)).fetchall()

    def find_all_by_group_key(self, group_key: str, within_days: int = 30,
                              now: datetime | None = None) -> list[sqlite3.Row]:
        since = now_iso((now or datetime.now(UTC)) - timedelta(days=within_days))
        return self.conn.execute(
            "SELECT * FROM jobs WHERE group_key = ? AND discovered_at >= ? ORDER BY discovered_at, id",
            (group_key, since)).fetchall()

    def jobs_by_status(self, *statuses: Status | str) -> list[sqlite3.Row]:
        """Jobs in any of ``statuses``, oldest first. Unknown statuses raise ``ValueError``."""
        values = [Status.parse(s).value for s in statuses]
        if not values:
            return []
        marks = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY discovered_at, id", values
        ).fetchall()

    def alert_jobs_first(self, jobs: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
        sources = get_args(AlertSource)
        marks = ", ".join("?" for _ in sources)
        alert_ids = {
            row[0] for row in self.conn.execute(
                f"SELECT DISTINCT job_id FROM job_sources WHERE source IN ({marks})", sources
            )
        }
        return sorted(jobs, key=lambda job: job["id"] not in alert_ids)

    # --- sources and locations --------------------------------------------

    def add_source(
        self,
        job_id: str,
        source: str,
        source_job_id: str | None = None,
        url: str | None = None,
        raw: Any = None,
    ) -> bool:
        """Attach a source posting to a job. Returns False if (source, id) already exists."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO job_sources (job_id, source, source_job_id, url, raw, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, source, source_job_id, url, _json(raw), now_iso()),
            )
        return cur.rowcount > 0

    def sources(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM job_sources WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()

    def add_location(
        self, job_id: str, city: str, source_job_id: str | None = None, url: str | None = None
    ) -> bool:
        """Link an additional city of a multi-city posting. Returns False if already linked."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO job_locations (job_id, city, source_job_id, url) "
                "VALUES (?, ?, ?, ?)",
                (job_id, city, source_job_id, url),
            )
        return cur.rowcount > 0

    def locations(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM job_locations WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()

    # --- status -----------------------------------------------------------

    def transition(self, job_id: str, to_status: Status | str, note: str | None = None) -> bool:
        """Move a job to ``to_status`` and write a timestamped ``status_events`` row.

        Raises ``ValueError`` for an unknown status and ``KeyError`` for an unknown job.
        Returns False (and writes nothing) if the job already has that status.
        """
        target = Status.parse(to_status)
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] == target.value:
            return False
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (target.value, stamp, job_id),
            )
            self.conn.execute(
                "INSERT INTO status_events (job_id, from_status, to_status, at, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, job["status"], target.value, stamp, note),
            )
        return True

    def history(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM status_events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()

    def set_needs_review(self, job_id: str, reasons: Sequence[str]) -> None:
        """Flag (non-empty ``reasons``) or clear (empty) the needs-review flag."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET needs_review = ?, needs_review_reasons = ?, updated_at = ? WHERE id = ?",
                (1 if reasons else 0, _json(list(reasons)) if reasons else None, now_iso(), job_id),
            )
        if cur.rowcount == 0:
            raise KeyError(job_id)

    # --- evaluations and applications -------------------------------------

    def add_evaluation(
        self,
        job_id: str,
        *,
        fit_score: int | None,
        decision: str | None,
        provider: str | None = None,
        model: str | None = None,
        payload: Any = None,
    ) -> int:
        """Store an evaluation and make it the job's current one."""
        with self.conn:
            self.conn.execute("UPDATE evaluations SET is_current = 0 WHERE job_id = ?", (job_id,))
            cur = self.conn.execute(
                "INSERT INTO evaluations (job_id, created_at, provider, model, fit_score, decision, "
                "payload, is_current) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (job_id, now_iso(), provider, model, fit_score, decision, _json(payload)),
            )
        return int(cur.lastrowid or 0)

    def current_evaluation(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM evaluations WHERE job_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()

    def upsert_application(self, job_id: str, **fields: Any) -> None:
        """Create or update the application row. ``assumed_skills`` may be a list."""
        unknown = set(fields) - APPLICATION_FIELDS
        if unknown:
            raise TypeError(f"unknown application fields: {sorted(unknown)}")
        if isinstance(fields.get("assumed_skills"), (list, tuple)):
            fields["assumed_skills"] = _json(list(fields["assumed_skills"]))
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO applications (job_id) VALUES (?)", (job_id,))
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                self.conn.execute(
                    f"UPDATE applications SET {sets} WHERE job_id = ?", [*fields.values(), job_id]
                )

    def get_application(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()

    # --- runs -------------------------------------------------------------

    def start_run(self, run_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (id, started_at) VALUES (?, ?)", (run_id, now_iso())
            )

    def close_run(self, run_id: str) -> None:
        """Mark a run finished if nothing else did (commands other than ``run``)."""
        with self.conn:
            self.conn.execute("UPDATE runs SET finished_at = ? WHERE id = ? AND finished_at IS NULL",
                              (now_iso(), run_id))

    def finish_run(self, run_id: str, summary: Any = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
                (now_iso(), _json(summary), run_id),
            )


class DbUsageRecorder:
    """``UsageRecorder`` that writes ``llm_usage`` rows."""

    def __init__(self, conn: sqlite3.Connection, run_id: str | None = None) -> None:
        self.conn = conn
        self.run_id = run_id

    def record(self, usage: Usage) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, "
                "cached_input_tokens, cache_write_tokens, created_at, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usage.provider,
                    usage.model,
                    usage.task,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_input_tokens,
                    usage.cache_write_tokens,
                    now_iso(usage.created_at),
                    self.run_id,
                ),
            )
