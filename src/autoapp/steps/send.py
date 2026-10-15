"""Send step: deliver prepared email applications (opt-in, with every safeguard).

Uses the ``email.eml`` written by prepare (its Message-ID is the idempotency key).
``dry_run`` performs every check and reports what would be sent, without contacting
the SMTP server.
"""

from __future__ import annotations

import copy
import email
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from autoapp.db.status import Status
from autoapp.mail.connection import MailAuthError, Mailer, MailError, MailSentNoCopyError, SmtpMailer
from autoapp.mail.send import check_eligibility, reconcile_pending, send_application
from autoapp.steps.context import RunContext

BUNDLE = "Bewerbungsmappe.pdf"


@dataclass
class SendSummary:
    sent: list[str] = field(default_factory=list)
    would_send: list[str] = field(default_factory=list)
    not_sent: dict[str, list[str]] = field(default_factory=dict)
    flagged: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auth_failed: bool = False


def _folder(ctx: RunContext, job_id: str) -> Path | None:
    app = ctx.repo.get_application(job_id)
    return Path(app["folder"]) if app and app["folder"] else None


def load_message(folder: Path) -> EmailMessage:
    return email.message_from_bytes((folder / "email.eml").read_bytes(), policy=policy.default)  # type: ignore[return-value]


def default_mailer(ctx: RunContext) -> Mailer:
    account = ctx.config.mail.sending
    if account is None:
        raise RuntimeError("no sending mailbox configured (mail.sending); run `autoapp setup --section email`")
    password = os.environ.get(account.password_env or "", "")
    if not password:
        raise RuntimeError(f"{account.password_env} is not set; add your app password to workspace/.env")
    return SmtpMailer(account, password)


def _flag(ctx: RunContext, job_id: str, reason: str) -> None:
    job = ctx.repo.get_job(job_id)
    reasons = json.loads(job["needs_review_reasons"] or "[]") if job else []
    if reason not in reasons:
        ctx.repo.set_needs_review(job_id, [*reasons, reason])


def send_applications(
    ctx: RunContext,
    job_ids: list[str] | None = None,
    *,
    manual: bool = False,
    mailer: Mailer | None = None,
    confirm: Callable[[str, EmailMessage], bool] | None = None,
) -> SendSummary:
    """Send eligible email applications.

    ``manual`` is the ``send <id>`` command: the user has asked for this specific
    application, so the auto-send switch, score, free-mail rule, cap and cooldown are
    skipped; ``confirm(job_id, message)`` is asked before each send.
    """
    s = SendSummary()
    repo = ctx.repo
    if not ctx.dry_run:
        if ctx.source_mode == "full":
            s.unconfirmed = reconcile_pending(repo)
        else:
            pending = ctx.conn.execute(
                "SELECT DISTINCT j.* FROM jobs j JOIN send_attempts s ON s.job_id = j.id "
                "WHERE s.state = 'pending'"
            ).fetchall()
            selected = {job["id"] for job in ctx.jobs_for_sources(pending)}
            s.unconfirmed = reconcile_pending(repo, job_ids=selected)
    if job_ids is None:
        job_ids = [j["id"] for j in ctx.jobs_for_sources(repo.jobs_by_status(Status.PREPARED))
                   if (repo.get_application(j["id"]) or {"channel": None})["channel"] == "email"]
    policy_ = ctx.config.email
    for job_id in job_ids:
        job = repo.get_job(job_id)
        if job is not None and not ctx.jobs_for_sources([job]):
            continue
        folder = _folder(ctx, job_id)
        bundle = folder / BUNDLE if folder else None
        elig = check_eligibility(repo, job_id, policy_, bundle=bundle, manual=manual)
        if folder is not None and not (folder / "email.eml").exists():
            elig.ok = False
            elig.reasons.append("email.eml is missing; run `autoapp render` for this job")
        job_row = repo.get_job(job_id)
        if not elig.ok:
            s.not_sent[job_id] = elig.reasons
            if job_row is not None:
                ctx.emit("email_held", job_row, detail="; ".join(elig.reasons)[:200])
            if elig.flag_review and not ctx.dry_run:
                for r in elig.reasons:
                    if "check it by hand" in r:
                        _flag(ctx, job_id, r)
                s.flagged.append(job_id)
            continue
        msg = load_message(folder)  # type: ignore[arg-type]
        if ctx.dry_run:
            s.would_send.append(job_id)
            ctx.emit("email_would_send", job_row, detail=str(msg["To"] or ""))
            continue
        if confirm is not None and not confirm(job_id, msg):
            s.not_sent[job_id] = ["not confirmed"]
            continue
        if mailer is None:
            try:
                mailer = default_mailer(ctx)
            except RuntimeError as err:
                s.errors.append(str(err))
                break
        result = send_application(repo, job_id, msg, mailer)
        if result.sent:
            s.sent.append(job_id)
            ctx.emit("email_sent", job_row, detail=str(msg["To"] or ""))
            if result.warning:
                s.warnings.append(f"{job_id}: {result.warning}")
        else:
            s.errors.append(f"{job_id}: {result.error}")
            ctx.emit("email_failed", job_row, detail=str(result.error or "")[:200])
            if result.auth_failed:
                s.auth_failed = True
                break  # the login will fail for every other job too
    return s


# --- test mode: the real application email, delivered to yourself --------------------------------

TEST_PREFIX = "TEST_RUN: "


@dataclass
class SelfTestResult:
    sent_to: str | None = None
    job_id: str | None = None
    kind: str = "application"  # "application" (a prepared job's real email) or "connection" (plain test mail)
    original_to: str | None = None
    evidence: str | None = None  # where in the ad the address was found
    warning: str | None = None
    error: str | None = None


def address_evidence(description: str | None, address: str, width: int = 90) -> str | None:
    """The stretch of the ad around the address, so you can see where it came from."""
    text = " ".join((description or "").split())
    pos = text.lower().find(address.lower())
    if pos < 0:
        return None
    start, end = max(0, pos - width), min(len(text), pos + len(address) + width)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def banner(original_to: str) -> str:
    return f"This email is what would be sent to {original_to} in a real run."


def build_test_copy(msg: EmailMessage, own_address: str) -> EmailMessage:
    """The application email as it would go out, addressed to ``own_address`` instead.

    The subject gets ``TEST_RUN: `` in front and the body starts with a line saying where the
    email would have gone. The letter text and the attachment are untouched. Cc, Bcc and
    Reply-To are dropped, so nobody but ``own_address`` can receive it.
    """
    out = copy.deepcopy(msg)
    original_to = str(msg["To"] or "").strip()
    for header in ("To", "Cc", "Bcc", "Reply-To", "Message-ID", "Date", "Subject", "X-Autoapp-Test"):
        del out[header]
    out["To"] = own_address
    out["Subject"] = TEST_PREFIX + str(msg["Subject"] or "")
    out["Date"] = formatdate(localtime=True)
    out["Message-ID"] = make_msgid(domain=own_address.rsplit("@", 1)[-1])
    out["X-Autoapp-Test"] = "1"
    body = out.get_body(("plain",))
    if body is not None:
        body.set_content(banner(original_to) + "\n\n" + body.get_content(), charset="utf-8", cte="quoted-printable")
    return out


def _connection_test_mail(own_address: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = own_address
    msg["To"] = own_address
    msg["Subject"] = TEST_PREFIX + "autoapp can send email"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=own_address.rsplit("@", 1)[-1])
    msg["X-Autoapp-Test"] = "1"
    msg.set_content("This is a test from autoapp. Sending from your mailbox works.\n", charset="utf-8")
    return msg


def _latest_prepared_email_job(ctx: RunContext) -> str | None:
    repo = ctx.repo
    for job in reversed(ctx.jobs_for_sources(repo.jobs_by_status(Status.PREPARED))):
        app = repo.get_application(job["id"])
        folder = Path(app["folder"]) if app and app["folder"] else None
        if app and app["channel"] == "email" and folder and (folder / "email.eml").exists():
            return job["id"]
    return None


def send_test_email(ctx: RunContext, job_id: str | None = None, *, mailer: Mailer | None = None) -> SelfTestResult:
    """Deliver one job's application email to yourself instead of the employer.

    One email arrives: the subject is ``TEST_RUN: `` plus the normal subject, and the body starts
    with "This email is what would be sent to <address> in a real run." Nothing is recorded: no
    send attempt, status change, cap or cooldown effect. Without ``job_id`` the newest prepared
    email application is used, or one plain test mail when there is none.
    """
    account = ctx.config.mail.sending
    if account is None:
        return SelfTestResult(error="no sending mailbox configured (mail.sending); run `autoapp setup --section email`")
    own = account.address
    result = SelfTestResult(sent_to=own)
    if job_id is None:
        job_id = _latest_prepared_email_job(ctx)
    if job_id is None:
        result.kind = "connection"
        msg = _connection_test_mail(own)
    else:
        job = ctx.repo.get_job(job_id)
        if job is None:
            return SelfTestResult(error=f"unknown job {job_id}")
        if not ctx.jobs_for_sources([job]):
            return SelfTestResult(error=f"job {job_id} is excluded by this run source mode")
        app = ctx.repo.get_application(job_id)
        folder = _folder(ctx, job_id)
        if folder is None or not (folder / "email.eml").exists():
            return SelfTestResult(error=f"job {job_id} has no email.eml; run `autoapp prepare --job {job_id}` first")
        original = load_message(folder)
        recipient = str(original["To"] or "").strip()
        if not recipient or "@" not in recipient:
            return SelfTestResult(error=f"job {job_id}: the prepared email has no recipient address")
        if app is not None and app["contact_email"] and app["contact_email"].lower() != recipient.lower():
            return SelfTestResult(error=f"job {job_id}: email.eml is addressed to {recipient} but the application "
                                        f"says {app['contact_email']}; run `autoapp render {job_id}`")
        result.job_id, result.original_to = job_id, recipient
        result.evidence = address_evidence(job["description"], recipient)
        if result.evidence is None:
            result.warning = f"{recipient} was not found word for word in the stored ad text; check it by hand"
        msg = build_test_copy(original, own)
    try:
        mailer = mailer or default_mailer(ctx)
        mailer.send(msg, to_addrs=[own], keep_copy=False)  # the envelope names only you
    except MailSentNoCopyError as err:
        result.warning = str(err)
    except (MailError, RuntimeError) as err:
        result.error = str(err)
    return result


@dataclass
class TestRunSummary:
    """Test-run mode: what a real automatic run would send, delivered to yourself."""

    sent: dict[str, str] = field(default_factory=dict)  # job ID -> the real recipient
    would_send: dict[str, str] = field(default_factory=dict)
    not_sent: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    own_address: str | None = None
    auth_failed: bool = False


def send_test_applications(ctx: RunContext, *, mailer: Mailer | None = None) -> TestRunSummary:
    """Apply the automatic-mode rules, then send each email a real run would send to yourself.

    The daily cap counts real sends plus the test emails of this run. Nothing is recorded:
    no send attempts, no status change, so a later real run can still send each job.
    """
    s = TestRunSummary()
    repo = ctx.repo
    account = ctx.config.mail.sending
    if account is None:
        s.errors.append("no sending mailbox configured (mail.sending); run `autoapp setup --section email`")
        return s
    own = s.own_address = account.address
    cap = ctx.config.email.daily_cap
    from datetime import UTC, datetime

    from autoapp.mail.send import sends_today

    real_today = sends_today(repo, datetime.now(UTC))
    job_ids = [j["id"] for j in ctx.jobs_for_sources(repo.jobs_by_status(Status.PREPARED))
               if (repo.get_application(j["id"]) or {"channel": None})["channel"] == "email"]
    sample = ctx.sample
    for job_id in job_ids:
        if sample is not None and len(s.sent) + len(s.would_send) >= sample.test_emails:
            break  # the quick test sends at most this many test emails
        job = repo.get_job(job_id)
        folder = _folder(ctx, job_id)
        elig = check_eligibility(repo, job_id, ctx.config.email, bundle=folder / BUNDLE if folder else None)
        reasons = list(elig.reasons)
        if sample is not None and sample.ignore_threshold:
            # The quick test prepared its jobs whatever their score; the mail goes only to you, so the score is no bar.
            reasons = [r for r in reasons if not r.startswith(("decision is not apply", "fit score "))]
        if folder is not None and not (folder / "email.eml").exists():
            reasons.append("email.eml is missing; run `autoapp render` for this job")
        if not any("daily cap" in r for r in reasons) and real_today + len(s.sent) + len(s.would_send) >= cap:
            reasons.append(f"daily cap of {cap} reached")
        if reasons:
            s.not_sent[job_id] = reasons
            ctx.emit("email_held", job, detail="would not be sent: " + "; ".join(reasons)[:180])
            continue
        original = load_message(folder)  # type: ignore[arg-type]
        recipient = str(original["To"] or "")
        if ctx.dry_run:
            s.would_send[job_id] = recipient
            ctx.emit("test_would_send", job, detail=f"{own} (a real run would send to {recipient})")
            continue
        msg = build_test_copy(original, own)
        try:
            mailer = mailer or default_mailer(ctx)
            mailer.send(msg, to_addrs=[own], keep_copy=False)  # the envelope names only you
        except MailSentNoCopyError:
            pass
        except MailError as err:
            s.errors.append(f"{job_id}: {err}")
            if isinstance(err, MailAuthError):
                s.auth_failed = True
                break
            continue
        except RuntimeError as err:
            s.errors.append(str(err))
            break
        s.sent[job_id] = recipient
        ctx.emit("test_sent", job, detail=f"{own} (a real run would send to {recipient})")
    return s
