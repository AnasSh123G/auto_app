"""Logic behind ``status``, ``list`` and ``mark`` (pure functions plus rich renderers)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from autoapp.db.repo import Repo, now_iso
from autoapp.db.status import Status
from autoapp.llm.base import Usage
from autoapp.llm.cost import CostSummary, PriceTable, format_cost

BERLIN = ZoneInfo("Europe/Berlin")

# Statuses where an application is closed; a needs_review flag there needs no attention.
_CLOSED = (
    Status.SKIPPED,
    Status.FILTERED,
    Status.SENT,
    Status.SUBMITTED,
    Status.INTERVIEW,
    Status.OFFER,
    Status.REJECTED,
    Status.WITHDRAWN,
    Status.EXPIRED,
)


@dataclass
class StatusSummary:
    counts: dict[str, int]
    sent_today: int
    review_queue: list[sqlite3.Row] = field(default_factory=list)
    cost_today: CostSummary | None = None
    cost_last_run: CostSummary | None = None


def _usages(rows: list[sqlite3.Row]) -> list[Usage]:
    return [
        Usage(
            provider=r["provider"],
            model=r["model"],
            task=r["task"],
            input_tokens=r["input_tokens"],
            output_tokens=r["output_tokens"],
            cached_input_tokens=r["cached_input_tokens"],
            cache_write_tokens=r["cache_write_tokens"],
        )
        for r in rows
    ]


def usage_costs(
    conn: sqlite3.Connection, prices: PriceTable, start: datetime, end: datetime
) -> tuple[CostSummary, CostSummary | None]:
    """Estimated LLM cost between ``start`` and ``end`` and of the most recent run."""
    today = prices.summarize(
        _usages(
            conn.execute(
                "SELECT * FROM llm_usage WHERE created_at >= ? AND created_at < ?",
                (now_iso(start), now_iso(end)),
            ).fetchall()
        )
    )
    last = conn.execute("SELECT id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    last_run = None
    if last is not None:
        last_run = prices.summarize(
            _usages(conn.execute("SELECT * FROM llm_usage WHERE run_id = ?", (last["id"],)).fetchall())
        )
    return today, last_run


def status_summary(
    conn: sqlite3.Connection, now: datetime | None = None, prices: PriceTable | None = None
) -> StatusSummary:
    """Counts per status, sends today (Europe/Berlin day) and the review queue."""
    counts = {s.value: 0 for s in Status}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
        counts[r["status"]] = r["n"]

    local = (now or datetime.now(UTC)).astimezone(BERLIN)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=BERLIN)
    sent_today = conn.execute(
        "SELECT COUNT(DISTINCT job_id) FROM status_events WHERE to_status = ? AND at >= ? AND at < ?",
        (Status.SENT.value, now_iso(start), now_iso(end)),
    ).fetchone()[0]

    closed = [s.value for s in _CLOSED]
    queue = conn.execute(
        f"SELECT * FROM jobs WHERE status = ? OR (needs_review = 1 AND status NOT IN "
        f"({', '.join('?' for _ in closed)})) ORDER BY discovered_at, id",
        [Status.REVIEW.value, *closed],
    ).fetchall()
    cost_today, cost_last_run = usage_costs(conn, prices or PriceTable.load(), start, end)
    return StatusSummary(
        counts=counts,
        sent_today=sent_today,
        review_queue=queue,
        cost_today=cost_today,
        cost_last_run=cost_last_run,
    )


def list_by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    """Jobs with ``status``; an unknown status raises ``ValueError``."""
    return Repo(conn).jobs_by_status(Status.parse(status))


def _override_decision(conn: sqlite3.Connection, job_id: str, decision: str) -> None:
    """Record the user's decision on the current evaluation (payload included), so later
    steps (assumed skills, auto-send eligibility) treat the job as the user decided."""
    import json

    row = conn.execute("SELECT id, payload FROM evaluations WHERE job_id = ? AND is_current = 1", (job_id,)).fetchone()
    if row is None:
        return
    payload = json.loads(row["payload"]) if row["payload"] else None
    if isinstance(payload, dict):
        payload["decision"] = decision
        payload.setdefault("notes", []).append(f"decision set to {decision} by the user")
    with conn:
        conn.execute("UPDATE evaluations SET decision = ?, payload = ? WHERE id = ?",
                     (decision, json.dumps(payload) if payload is not None else None, row["id"]))


def mark(conn: sqlite3.Connection, job_id: str, status: str, note: str | None = None) -> Status:
    """Change a job's status by hand and store ``note`` in both history and the job's notes.

    A note is appended to existing notes (separated by ``; ``) so nothing is lost.
    Raises ``ValueError`` for an unknown status and ``KeyError`` for an unknown job.
    """
    repo = Repo(conn)
    # ``apply`` / ``skip`` promote or demote a job from the review queue.
    target = Status.parse({"apply": "approved", "skip": "skipped"}.get(status.strip().lower(), status))
    job = repo.get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    repo.transition(job_id, target, note=note or "marked manually")
    decision = {"apply": "apply", "skip": "skip"}.get(status.strip().lower())
    if decision:
        _override_decision(conn, job_id, decision)
    if note:
        existing = job["notes"]
        repo.update_job(job_id, notes=f"{existing}; {note}" if existing else note)
    return target


def render_status(summary: StatusSummary) -> RenderableType:
    """Rich renderable for the ``status`` command."""
    counts = Table("Status", "Jobs", title="Jobs per status")
    for name, n in summary.counts.items():
        if n:
            counts.add_row(name, str(n))
    if not any(summary.counts.values()):
        counts.add_row("(no jobs yet)", "0")

    parts: list[RenderableType] = [
        counts,
        Text(f"Applications sent today: {summary.sent_today}"),
        Text(f"Review queue: {len(summary.review_queue)}"),
    ]
    if summary.review_queue:
        parts.append(render_jobs(summary.review_queue, title="Review queue"))
    if summary.cost_today is not None:
        parts.append(Text(f"Estimated LLM cost today: {format_cost(summary.cost_today.total)}"))
    if summary.cost_last_run is not None:
        parts.append(Text(f"Estimated LLM cost of the last run: {format_cost(summary.cost_last_run.total)}"))
    return Group(*parts)


def render_jobs(jobs: list[sqlite3.Row], title: str | None = None) -> Table:
    """Rich table of jobs."""
    table = Table("ID", "Status", "Company", "Title", "Location", "Review", title=title)
    for j in jobs:
        table.add_row(
            j["id"],
            j["status"],
            j["company"] or "",
            j["title"] or "",
            j["location"] or "",
            "yes" if j["needs_review"] else "",
        )
    return table
