"""Redo everything that has not been sent: those jobs go back to ``new`` and are processed again.

Jobs that may have reached an employer are never touched: ``sent``, ``submitted`` and everything after
(``interview``, ``offer``, ``rejected``, ``withdrawn``, ``expired``), plus ``send_unconfirmed`` (the mail may have
left) and ``drafted`` (a draft already sits in your mailbox). Jobs the rules filtered out stay filtered.

A redone job is evaluated again, its folder rebuilt in place (the old letter stays next to the new one with a
timestamp, your other files are not deleted) and its application page looked for again. The database is backed up
first. The usual per-run limits still apply, so a big redo continues with the next normal runs: whatever is still
``new`` is simply picked up there.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from autoapp.db.status import Status

REDO = (Status.DESCRIPTION_UNAVAILABLE, Status.EVALUATION_FAILED, Status.SKIPPED, Status.REVIEW, Status.APPROVED,
        Status.PREPARED)
PROTECTED_NOTE = (Status.SEND_UNCONFIRMED, Status.DRAFTED)
FORM_FIELDS = ("form_entry_url", "form_reached_url", "form_status", "form_checked_at", "form_note")


@dataclass
class RedoPlan:
    counts: dict[str, int] = field(default_factory=dict)  # status -> jobs that would be redone
    kept: dict[str, int] = field(default_factory=dict)  # drafted / send_unconfirmed jobs left alone
    job_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.job_ids)


def plan(conn: sqlite3.Connection) -> RedoPlan:
    result = RedoPlan()
    for row in conn.execute("SELECT id, status FROM jobs ORDER BY discovered_at, id"):
        status = row["status"]
        if status in {s.value for s in REDO}:
            result.counts[status] = result.counts.get(status, 0) + 1
            result.job_ids.append(row["id"])
        elif status in {s.value for s in PROTECTED_NOTE}:
            result.kept[status] = result.kept.get(status, 0) + 1
    return result


def describe(p: RedoPlan) -> list[str]:
    lines = [f"{p.total} jobs would be processed again: " + (", ".join(f"{n} {s}" for s, n in p.counts.items()) or "none")]
    if p.kept:
        lines.append("left alone (a draft exists or the send is unconfirmed): "
                     + ", ".join(f"{n} {s}" for s, n in p.kept.items()))
    lines.append("sent, submitted and later jobs, and jobs filtered by your rules, are not touched")
    return lines


def backup_database(ctx, now: datetime | None = None) -> str:  # noqa: ANN001 - RunContext
    """Copy the database to ``backups/redo-<time>/autoapp.db``; returns the path."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = ctx.ws.backups_dir / f"redo-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    copy = sqlite3.connect(str(target / "autoapp.db"))
    try:
        ctx.conn.backup(copy)
    finally:
        copy.close()
    return str(target / "autoapp.db")


def redo_unsent(ctx) -> RedoPlan:  # noqa: ANN001 - RunContext
    """Reset every redoable job to ``new`` and forget what its old folder and form lookup concluded."""
    p = plan(ctx.conn)
    for job_id in p.job_ids:
        ctx.repo.transition(job_id, Status.NEW, note="redo: processed again")
        ctx.repo.set_needs_review(job_id, [])
        if ctx.repo.get_application(job_id) is not None:
            # No prepared_at: the folder counts as unfinished, so preparing rebuilds it (keeping the old letter).
            ctx.repo.upsert_application(job_id, prepared_at=None, **dict.fromkeys(FORM_FIELDS))
    return p
