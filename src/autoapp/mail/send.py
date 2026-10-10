"""Email submission: eligibility, limits and idempotent sending (design decision 11).

Safety properties:

* a send attempt with a pre-generated Message-ID is recorded *before* SMTP is contacted;
* a ``pending`` attempt that is older than ``STALE_AFTER`` and never confirmed turns the
  job into ``send_unconfirmed`` and is never retried automatically;
* nothing is sent twice for a job, and the daily cap and company cooldown apply;
* an authentication failure stops all sending for the run.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from autoapp.config.models import EmailPolicy
from autoapp.db.repo import Repo, now_iso
from autoapp.db.status import Status
from autoapp.discovery.normalize import norm_company
from autoapp.mail.connection import MailAuthError, Mailer, MailError, MailSentNoCopyError

BERLIN = ZoneInfo("Europe/Berlin")
STALE_AFTER = timedelta(minutes=10)


@dataclass
class Eligibility:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    flag_review: bool = False  # true when a human should look (e.g. free-mail recipient)


@dataclass
class SendResult:
    job_id: str
    sent: bool
    message_id: str | None = None
    error: str | None = None
    warning: str | None = None
    auth_failed: bool = False


def _domain_is_free_mail(address: str, patterns: list[str]) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(fnmatch.fnmatch(domain, p.lower()) for p in patterns)


def _berlin_day_bounds(now: datetime) -> tuple[str, str]:
    local = now.astimezone(BERLIN)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=BERLIN)
    return now_iso(start), now_iso(end)


def sends_today(repo: Repo, now: datetime) -> int:
    start, end = _berlin_day_bounds(now)
    return repo.conn.execute(
        "SELECT COUNT(*) FROM send_attempts WHERE state = 'sent' AND updated_at >= ? AND updated_at < ?",
        (start, end),
    ).fetchone()[0]


def _attempts(repo: Repo, job_id: str) -> list:
    return repo.conn.execute("SELECT * FROM send_attempts WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()


def last_send_to_company(repo: Repo, company: str) -> datetime | None:
    target = norm_company(company)
    rows = repo.conn.execute(
        "SELECT j.company, s.updated_at FROM send_attempts s JOIN jobs j ON j.id = s.job_id "
        "WHERE s.state IN ('sent', 'pending')"
    ).fetchall()
    times = [datetime.fromisoformat(r["updated_at"]) for r in rows if norm_company(r["company"]) == target]
    return max(times) if times else None


def check_eligibility(
    repo: Repo,
    job_id: str,
    policy: EmailPolicy,
    *,
    bundle: Path | None,
    now: datetime | None = None,
    manual: bool = False,
) -> Eligibility:
    """Whether a prepared email application may be sent now.

    It is only called in automatic mode (or by ``send <id>``). ``manual`` (the ``send``
    command, confirmed by the user) skips the minimum score, the free-mail rule, the daily cap and the cooldown; the duplicate
    guard, the attempt limit and the basic checks still apply.
    """
    now = now or datetime.now(UTC)
    reasons: list[str] = []
    flag = False
    job = repo.get_job(job_id)
    if job is None:
        return Eligibility(False, [f"unknown job {job_id}"])
    app = repo.get_application(job_id)
    ev = repo.current_evaluation(job_id)

    drafted_ok = manual and job["status"] == Status.DRAFTED.value  # `send <id>` after a draft, confirmed
    if job["status"] != Status.PREPARED.value and not drafted_ok:
        reasons.append(f"status is {job['status']}, not prepared")
    if ev is None or ev["decision"] != "apply":
        reasons.append("decision is not apply")
    if app is None or app["channel"] != "email" or not app["contact_email"]:
        reasons.append("the ad does not take applications by email")
    elif not manual and _domain_is_free_mail(app["contact_email"], policy.free_mail_domains):
        reasons.append(f"recipient {app['contact_email']} is a free-mail address; check it by hand")
        flag = True
    if not manual and ev is not None and (ev["fit_score"] or 0) < policy.min_score:
        reasons.append(f"fit score {ev['fit_score']} is below email.min_score {policy.min_score}")
    if job["needs_review"]:
        reasons.append("flagged needs_review: " + "; ".join(json.loads(job["needs_review_reasons"] or "[]")))
    if bundle is None or not bundle.exists():
        reasons.append("Bewerbungsmappe.pdf is missing")
    elif bundle.stat().st_size > policy.max_bundle_mb * 1024 * 1024:
        reasons.append(f"Bewerbungsmappe.pdf is larger than {policy.max_bundle_mb} MB")

    attempts = _attempts(repo, job_id)
    if any(a["state"] in ("sent", "pending") for a in attempts):
        reasons.append("already sent or a send is unconfirmed for this job")
    failed = sum(1 for a in attempts if a["state"] == "failed")
    if failed >= policy.max_attempts:
        reasons.append(f"gave up after {failed} failed attempts")

    if not manual and app is not None:
        if sends_today(repo, now) >= policy.daily_cap:
            reasons.append(f"daily cap of {policy.daily_cap} reached")
        last = last_send_to_company(repo, job["company"])
        if last is not None and now - last < timedelta(days=policy.company_cooldown_days):
            days = (now - last).days
            reasons.append(
                f"an application to {job['company']} was sent {days} days ago "
                f"(cooldown {policy.company_cooldown_days} days); check it by hand"
            )
            flag = True
    return Eligibility(ok=not reasons, reasons=reasons, flag_review=flag)


def reconcile_pending(repo: Repo, now: datetime | None = None) -> list[str]:
    """Jobs whose send was started but never confirmed become ``send_unconfirmed``."""
    now = now or datetime.now(UTC)
    cutoff = now_iso(now - STALE_AFTER)
    rows = repo.conn.execute(
        "SELECT DISTINCT job_id FROM send_attempts WHERE state = 'pending' AND created_at < ?", (cutoff,)
    ).fetchall()
    changed = []
    for r in rows:
        job = repo.get_job(r["job_id"])
        if job is not None and job["status"] != Status.SEND_UNCONFIRMED.value:
            repo.transition(r["job_id"], Status.SEND_UNCONFIRMED, note="send started but never confirmed; check Sent")
            changed.append(r["job_id"])
    return changed


def _set_attempt(repo: Repo, attempt_id: int, state: str, error: str | None = None) -> None:
    with repo.conn:
        repo.conn.execute(
            "UPDATE send_attempts SET state = ?, error = ?, updated_at = ? WHERE id = ?",
            (state, error, now_iso(), attempt_id),
        )


def send_application(repo: Repo, job_id: str, msg: EmailMessage, mailer: Mailer) -> SendResult:
    """Record the attempt, send, then record the outcome. Never retries on its own."""
    message_id = msg["Message-ID"]
    if not message_id:
        raise ValueError("the message needs a Message-ID before sending")
    recipient = msg["To"]
    with repo.conn:
        cur = repo.conn.execute(
            "INSERT INTO send_attempts (job_id, message_id, state, created_at, updated_at, recipient) "
            "VALUES (?, ?, 'pending', ?, ?, ?)",
            (job_id, message_id, now_iso(), now_iso(), recipient),
        )
    attempt_id = cur.lastrowid
    warning = None
    try:
        mailer.send(msg)
    except MailSentNoCopyError as err:
        warning = str(err)  # it was sent; record it as sent
    except MailAuthError as err:
        _set_attempt(repo, attempt_id, "failed", str(err))
        return SendResult(job_id, False, message_id, error=str(err), auth_failed=True)
    except MailError as err:
        _set_attempt(repo, attempt_id, "failed", str(err))
        return SendResult(job_id, False, message_id, error=str(err))
    _set_attempt(repo, attempt_id, "sent", warning)
    repo.transition(job_id, Status.SENT, note=f"emailed to {recipient}")
    repo.upsert_application(job_id, applied_at=now_iso())
    return SendResult(job_id, True, message_id, warning=warning)
