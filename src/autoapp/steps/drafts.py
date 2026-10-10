"""Draft step: save prepared email applications as drafts (review-first runs, ``draft <id>``),
and ``check_drafts``: mark drafts the user sent by hand as ``sent`` (email-drafts spec).

Nothing here sends email. Drafting ignores the automatic-send rules (score, cap, cooldown,
free-mail) because the user reads every draft, but reports them as notes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import formatdate, make_msgid
from pathlib import Path

from autoapp.db.repo import now_iso
from autoapp.db.status import Status
from autoapp.mail.connection import MailAuthError, MailError
from autoapp.mail.drafts import DraftFolderNotFound, DraftStore, ImapDraftStore, imap_utf7_decode
from autoapp.mail.send import check_eligibility
from autoapp.steps.context import RunContext
from autoapp.steps.send import BUNDLE, load_message

# Reasons that block an automatic send but not a draft; shown as notes.
_NOTE_MARKERS = ("min_score", "daily cap", "cooldown", "free-mail")


@dataclass
class DraftSummary:
    saved: list[str] = field(default_factory=list)
    would_save: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    not_drafted: dict[str, list[str]] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    auth_failed: bool = False
    folder: str | None = None


@dataclass
class CheckSummary:
    sent: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    still_drafted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def default_store(ctx: RunContext) -> DraftStore:
    account = ctx.config.mail.sending
    if account is None:
        raise RuntimeError("no sending mailbox configured (mail.sending); run `autoapp setup --section email`")
    password = os.environ.get(account.password_env or "", "")
    if not password:
        raise RuntimeError(f"{account.password_env} is not set; add your app password to workspace/.env")
    return ImapDraftStore(account, password)


def current_draft(ctx: RunContext, job_id: str):
    return ctx.conn.execute("SELECT * FROM drafts WHERE job_id = ? AND state = 'saved' ORDER BY id DESC LIMIT 1",
                            (job_id,)).fetchone()


def draftable_jobs(ctx: RunContext) -> list[str]:
    out = []
    for job in ctx.repo.jobs_by_status(Status.PREPARED):
        app = ctx.repo.get_application(job["id"])
        if app is not None and app["channel"] == "email" and app["contact_email"]:
            out.append(job["id"])
    return out


def _notes(ctx: RunContext, job_id: str, folder: Path) -> list[str]:
    elig = check_eligibility(ctx.repo, job_id, ctx.config.email, bundle=folder / BUNDLE)
    return [r for r in elig.reasons if any(m in r for m in _NOTE_MARKERS)]


def _reasons_not_draftable(ctx: RunContext, job, again: bool) -> list[str]:
    reasons = []
    app = ctx.repo.get_application(job["id"])
    allowed = {Status.PREPARED.value} | ({Status.DRAFTED.value} if again else set())
    if job["status"] not in allowed:
        reasons.append(f"status is {job['status']}, not prepared")
    if app is None or app["channel"] != "email" or not app["contact_email"]:
        reasons.append("the ad does not take applications by email")
    if job["needs_review"]:
        reasons.append("flagged needs_review; fix the letter, run `autoapp render`, then draft it")
    folder = Path(app["folder"]) if app and app["folder"] else None
    if folder is None or not (folder / "email.eml").exists():
        reasons.append("email.eml is missing; run `autoapp render` for this job")
    return reasons


def draft_applications(ctx: RunContext, job_ids: list[str] | None = None, *, again: bool = False,
                       store: DraftStore | None = None,
                       store_factory: Callable[[RunContext], DraftStore] | None = None) -> DraftSummary:
    s = DraftSummary()
    repo = ctx.repo
    ids = job_ids if job_ids is not None else draftable_jobs(ctx)
    own_store = False
    try:
        for job_id in ids:
            job = repo.get_job(job_id)
            if job is None:
                s.not_drafted[job_id] = [f"unknown job {job_id}"]
                continue
            existing = current_draft(ctx, job_id)
            if existing is not None and not again:
                s.existing.append(job_id)
                ctx.emit("draft_exists", job, detail=f"draft already saved in {imap_utf7_decode(existing['folder'])}")
                continue
            reasons = _reasons_not_draftable(ctx, job, again)
            if reasons:
                s.not_drafted[job_id] = reasons
                if job_ids is None and job["needs_review"]:
                    ctx.emit("needs_review", job, detail="not drafted: " + reasons[-1])
                continue
            app = repo.get_application(job_id)
            folder = Path(app["folder"])
            notes = _notes(ctx, job_id, folder)
            if notes:
                s.notes[job_id] = notes
            note_text = (" (" + "; ".join(notes) + ")") if notes else ""
            if ctx.dry_run:
                s.would_save.append(job_id)
                ctx.emit("draft_would_save", job, detail=f"{app['contact_email']}{note_text}")
                continue
            if store is None:
                try:
                    store = (store_factory or default_store)(ctx)
                    own_store = True
                except RuntimeError as err:
                    s.errors.append(str(err))
                    break
            msg = load_message(folder)
            del msg["Date"]
            msg["Date"] = formatdate(localtime=True)
            if again:
                del msg["Message-ID"]
                msg["Message-ID"] = make_msgid(domain=str(app["contact_email"]).rsplit("@", 1)[-1])
            try:
                if s.folder is None:
                    s.folder = store.drafts_folder()
                if not store.has_message(s.folder, str(msg["Message-ID"])):
                    store.append_draft(s.folder, msg)
            except MailAuthError as err:
                s.errors.append(str(err) + "; run `autoapp setup --section email`")
                s.auth_failed = True
                break
            except DraftFolderNotFound as err:
                s.errors.append(str(err))
                break
            except MailError as err:
                s.errors.append(f"{job_id}: {err}")
                ctx.emit("error", job, detail=f"draft not saved: {str(err)[:160]}")
                continue
            with ctx.conn:
                if again:
                    ctx.conn.execute("UPDATE drafts SET state = 'replaced', replaced_at = ? WHERE job_id = ? "
                                     "AND state = 'saved'", (now_iso(), job_id))
                ctx.conn.execute(
                    "INSERT INTO drafts (job_id, message_id, folder, recipient, subject, state, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'saved', ?)",
                    (job_id, str(msg["Message-ID"]), s.folder, str(msg["To"]), str(msg["Subject"]), now_iso()),
                )
            shown = imap_utf7_decode(s.folder)
            if job["status"] != Status.DRAFTED.value:
                repo.transition(job_id, Status.DRAFTED, note=f"draft saved in {shown}" + (" (replaced)" if again else ""))
            s.saved.append(job_id)
            ctx.emit("draft_saved", job, detail=f"{app['contact_email']} (in {shown}){note_text}")
    finally:
        if own_store and store is not None:
            store.close()
    return s


def check_drafts(ctx: RunContext, *, store: DraftStore | None = None,
                 store_factory: Callable[[RunContext], DraftStore] | None = None) -> CheckSummary:
    """Mark ``drafted`` jobs whose email shows up in Sent as ``sent``. Read-only; skipped in dry runs."""
    s = CheckSummary()
    if ctx.dry_run:
        return s
    drafted = ctx.repo.jobs_by_status(Status.DRAFTED)
    if not drafted:
        return s
    own = store is None
    try:
        store = store or (store_factory or default_store)(ctx)
    except RuntimeError as err:
        s.errors.append(str(err))
        return s
    try:
        for job in drafted:
            d = current_draft(ctx, job["id"])
            if d is None:
                continue
            since = datetime.fromisoformat(d["created_at"])
            try:
                if store.find_sent(d["recipient"] or "", d["subject"] or "", since):
                    ctx.repo.transition(job["id"], Status.SENT, note="sent by you from the draft")
                    ctx.repo.upsert_application(job["id"], applied_at=now_iso())
                    s.sent.append(job["id"])
                    ctx.emit("email_sent", job, detail=f"{d['recipient']} (sent by you from the draft)", by_hand=True)
                elif not store.has_message(d["folder"], d["message_id"]):
                    s.missing.append(job["id"])
                    ctx.emit("warning", job, detail=f"draft missing; run `autoapp draft {job['id']} --again`")
                else:
                    s.still_drafted.append(job["id"])
            except MailAuthError as err:
                s.errors.append(str(err))
                break
            except MailError as err:
                s.errors.append(f"{job['id']}: {err}")
    finally:
        if own and store is not None:
            store.close()
    return s
