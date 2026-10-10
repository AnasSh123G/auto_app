"""Typed persistence for verified employers, careers locations, seeds, scans and listings."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from autoapp.employers.names import core_name, normalize_name


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Listing:
    """One public vacancy read from an employer's careers pages."""

    url: str
    title: str
    ref: str | None = None
    locations: list[str] = field(default_factory=list)
    work_mode: str | None = None
    description: str | None = None
    posted_at: str | None = None
    expires_at: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    apply_url: str | None = None


class EmployerRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- employers -----------------------------------------------------------------

    def create_employer(self, display_name: str, *, status: str = "unresolved", website: str | None = None,
                        provenance: str | None = None, legal_name: str | None = None,
                        evidence: dict[str, Any] | None = None, pinned: bool = False) -> str:
        employer_id = "emp-" + uuid.uuid4().hex[:10]
        now = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO employers (id, display_name, legal_name, website, status, provenance, pinned, evidence, "
                "checked_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (employer_id, display_name, legal_name, website, status, provenance, int(pinned),
                 json.dumps(evidence, ensure_ascii=False) if evidence else None, now, now, now),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO employer_aliases (employer_id, alias, norm, core, hint, source) "
                "VALUES (?, ?, ?, ?, '', ?)",
                (employer_id, display_name, normalize_name(display_name), core_name(display_name), provenance),
            )
        return employer_id

    def get(self, employer_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM employers WHERE id = ?", (employer_id,)).fetchone()

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM employers ORDER BY display_name, id").fetchall()

    def add_alias(self, employer_id: str, alias: str, *, hint: str = "", source: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO employer_aliases (employer_id, alias, norm, core, hint, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (employer_id, alias, normalize_name(alias), core_name(alias), hint.strip().casefold(), source),
            )

    def aliases(self, employer_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM employer_aliases WHERE employer_id = ? ORDER BY id",
                                 (employer_id,)).fetchall()

    def candidates(self, name: str) -> list[sqlite3.Row]:
        """Employers with an alias sharing the name's core. Callers must still disambiguate."""
        core = core_name(name)
        if not core:
            return []
        return self.conn.execute(
            "SELECT DISTINCT e.* FROM employers e JOIN employer_aliases a ON a.employer_id = e.id "
            "WHERE a.core = ? ORDER BY e.id", (core,)
        ).fetchall()

    def verify(self, employer_id: str, website: str, *, provenance: str, evidence: dict[str, Any] | None = None,
               legal_name: str | None = None, pinned: bool | None = None) -> None:
        with self.conn:
            self._forget_other_site_snapshot(employer_id, website)
            self.conn.execute(
                "UPDATE employers SET status = 'verified', website = ?, provenance = ?, evidence = ?, "
                "legal_name = COALESCE(?, legal_name), pinned = COALESCE(?, pinned), failure_reason = NULL, "
                "failure_at = NULL, checked_at = ?, updated_at = ? WHERE id = ?",
                (website, provenance, json.dumps(evidence, ensure_ascii=False) if evidence else None, legal_name,
                 None if pinned is None else int(pinned), _now(), _now(), employer_id),
            )

    def _forget_other_site_snapshot(self, employer_id: str, website: str) -> None:
        """A snapshot read from another website must not stand in for the new one (listings go with their scans)."""
        row = self.conn.execute("SELECT website FROM employers WHERE id = ?", (employer_id,)).fetchone()
        if row and row["website"] and row["website"].rstrip("/") != website.rstrip("/"):
            self._drop_snapshot(employer_id)

    def _drop_snapshot(self, employer_id: str) -> None:
        self.conn.execute("DELETE FROM employer_listings WHERE employer_id = ?", (employer_id,))
        self.conn.execute("DELETE FROM employer_scans WHERE employer_id = ?", (employer_id,))

    def record_failure(self, employer_id: str, reason: str, *, status: str | None = None,
                       evidence: dict[str, Any] | None = None) -> None:
        """Remember why an employer could not be resolved; a verified employer keeps its status (and its
        evidence: ``evidence`` replaces only an unverified employer's)."""
        with self.conn:
            self.conn.execute(
                "UPDATE employers SET failure_reason = ?, failure_at = ?, updated_at = ?, "
                "evidence = CASE WHEN status = 'verified' OR ? IS NULL THEN evidence ELSE ? END, "
                "status = CASE WHEN status = 'verified' THEN status ELSE COALESCE(?, status) END WHERE id = ?",
                (reason, _now(), _now(), *([json.dumps(evidence, ensure_ascii=False)] * 2 if evidence else [None, None]),
                 status, employer_id),
            )

    def withdraw(self, employer_id: str, status: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
        """A fresh lookup the user asked for no longer supports the saved site: forget it and its scope."""
        with self.conn:
            if not self.conn.execute("SELECT pinned FROM employers WHERE id = ?", (employer_id,)).fetchone()["pinned"]:
                self._drop_snapshot(employer_id)
            self.conn.execute(
                "UPDATE employers SET status = ?, website = NULL, provenance = NULL, evidence = ?, failure_reason = ?, "
                "failure_at = ?, checked_at = ?, updated_at = ? WHERE id = ? AND pinned = 0",
                (status, json.dumps(evidence, ensure_ascii=False) if evidence else None, reason, _now(), _now(), _now(),
                 employer_id),
            )
            self.conn.execute("DELETE FROM employer_sites WHERE employer_id = ? AND "
                              "(SELECT pinned FROM employers WHERE id = ?) = 0", (employer_id, employer_id))

    def clear_failure(self, employer_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE employers SET failure_reason = NULL, failure_at = NULL WHERE id = ?",
                              (employer_id,))

    def failure_is_fresh(self, row: sqlite3.Row, ttl_hours: int, now: datetime | None = None) -> bool:
        if not row["failure_at"] or not row["failure_reason"]:
            return False
        at = datetime.fromisoformat(row["failure_at"])
        return (now or datetime.now(UTC)) - at < timedelta(hours=ttl_hours)

    def invalidate(self, employer_id: str, reason: str) -> None:
        """A definitively invalid website (404/410 and no usable official entry); allows one rediscovery."""
        with self.conn:
            self.conn.execute(
                "UPDATE employers SET status = 'invalid', failure_reason = ?, failure_at = ?, updated_at = ? "
                "WHERE id = ? AND pinned = 0", (reason, _now(), _now(), employer_id))

    # --- sites -----------------------------------------------------------------------

    def add_site(self, employer_id: str, url: str, origin: str, *, kind: str, tenant_path: str = "",
                 referrer_url: str | None = None, evidence: dict[str, Any] | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO employer_sites (employer_id, url, origin, tenant_path, kind, referrer_url, evidence, "
                "status, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'ok', ?) "
                "ON CONFLICT(employer_id, url) DO UPDATE SET origin = excluded.origin, "
                "tenant_path = excluded.tenant_path, kind = excluded.kind, referrer_url = excluded.referrer_url, "
                "evidence = COALESCE(excluded.evidence, evidence), status = 'ok', failure_reason = NULL, "
                "checked_at = excluded.checked_at",
                (employer_id, url, origin, tenant_path, kind, referrer_url,
                 json.dumps(evidence, ensure_ascii=False) if evidence else None, _now()),
            )

    def sites(self, employer_id: str, *, kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM employer_sites WHERE employer_id = ? ORDER BY id",
                                 (employer_id,)).fetchall()
        return [r for r in rows if kinds is None or r["kind"] in kinds]

    def mark_site(self, employer_id: str, url: str, status: str, reason: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE employer_sites SET status = ?, failure_reason = ?, checked_at = ? "
                "WHERE employer_id = ? AND url = ?", (status, reason, _now(), employer_id, url))

    def drop_sites(self, employer_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM employer_sites WHERE employer_id = ?", (employer_id,))

    # --- seeds -----------------------------------------------------------------------

    def record_seed(self, job_id: str, outcome: str, *, employer_id: str | None = None,
                    posting_url: str | None = None, posting_ref: str | None = None, reason: str | None = None,
                    candidates: list[str] | None = None, replaced_application_url: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO employer_seeds (job_id, employer_id, outcome, posting_url, posting_ref, reason, "
                "candidates, replaced_application_url, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET employer_id = excluded.employer_id, outcome = excluded.outcome, "
                "posting_url = excluded.posting_url, posting_ref = excluded.posting_ref, reason = excluded.reason, "
                "candidates = excluded.candidates, "
                "replaced_application_url = COALESCE(employer_seeds.replaced_application_url, "
                "excluded.replaced_application_url), checked_at = excluded.checked_at",
                (job_id, employer_id, outcome, posting_url, posting_ref, reason,
                 json.dumps(candidates) if candidates else None, replaced_application_url, _now()),
            )

    def seed(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM employer_seeds WHERE job_id = ?", (job_id,)).fetchone()

    def seeds_for(self, employer_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM employer_seeds WHERE employer_id = ? ORDER BY job_id",
                                 (employer_id,)).fetchall()

    # --- scans and listings --------------------------------------------------------------

    def start_scan(self, employer_id: str, run_id: str | None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO employer_scans (employer_id, run_id, started_at, status) VALUES (?, ?, ?, 'partial')",
                (employer_id, run_id, _now()))
        return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, status: str, *, pages: int, openings: int, reason: str | None = None,
                    checkpoint: dict[str, Any] | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE employer_scans SET status = ?, finished_at = ?, pages = ?, openings = ?, reason = ?, "
                "checkpoint = ? WHERE id = ?",
                (status, _now(), pages, openings, reason, json.dumps(checkpoint) if checkpoint else None, scan_id))

    def latest_scan(self, employer_id: str, *, complete_only: bool = False) -> sqlite3.Row | None:
        sql = "SELECT * FROM employer_scans WHERE employer_id = ?"
        if complete_only:
            sql += " AND status = 'complete'"
        return self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", (employer_id,)).fetchone()

    def fresh_complete_scan(self, employer_id: str, ttl_hours: int, now: datetime | None = None) -> sqlite3.Row | None:
        scan = self.latest_scan(employer_id, complete_only=True)
        if scan is None or not scan["finished_at"]:
            return None
        age = (now or datetime.now(UTC)) - datetime.fromisoformat(scan["finished_at"])
        return scan if age < timedelta(hours=ttl_hours) else None

    def save_listings(self, employer_id: str, scan_id: int | None, listings: list[Listing]) -> int:
        """Upsert snapshot rows by (employer, url); returns how many were written. One transaction."""
        written = 0
        with self.conn:
            for item in listings:
                self.conn.execute(
                    "INSERT INTO employer_listings (employer_id, scan_id, url, ref, title, locations, work_mode, "
                    "description, posted_at, expires_at, salary_min, salary_max, apply_url, seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(employer_id, url) DO UPDATE SET scan_id = excluded.scan_id, ref = excluded.ref, "
                    "title = excluded.title, locations = excluded.locations, work_mode = excluded.work_mode, "
                    "description = COALESCE(excluded.description, description), posted_at = excluded.posted_at, "
                    "expires_at = excluded.expires_at, salary_min = excluded.salary_min, "
                    "salary_max = excluded.salary_max, apply_url = excluded.apply_url, seen_at = excluded.seen_at",
                    (employer_id, scan_id, item.url, item.ref, item.title, json.dumps(item.locations),
                     item.work_mode, item.description, item.posted_at, item.expires_at, item.salary_min,
                     item.salary_max, item.apply_url, _now()),
                )
                written += 1
        return written

    def prune_listings(self, employer_id: str, scan_id: int) -> int:
        """After a complete scan the snapshot is exactly what it saw: closed postings and stale addresses go."""
        with self.conn:
            return self.conn.execute("DELETE FROM employer_listings WHERE employer_id = ? AND scan_id IS NOT ?",
                                     (employer_id, scan_id)).rowcount

    def listings(self, employer_id: str) -> list[Listing]:
        rows = self.conn.execute("SELECT * FROM employer_listings WHERE employer_id = ? ORDER BY id",
                                 (employer_id,)).fetchall()
        return [Listing(url=r["url"], title=r["title"], ref=r["ref"], locations=json.loads(r["locations"] or "[]"),
                        work_mode=r["work_mode"], description=r["description"], posted_at=r["posted_at"],
                        expires_at=r["expires_at"], salary_min=r["salary_min"], salary_max=r["salary_max"],
                        apply_url=r["apply_url"]) for r in rows]
