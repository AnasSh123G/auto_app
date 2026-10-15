"""`send --test`: the real application email, delivered to yourself, with no side effects."""

from __future__ import annotations

import pytest

from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer, MailAuthError, MailSentNoCopyError
from autoapp.steps.context import RunContext
from autoapp.steps.drafts import draft_applications
from autoapp.steps.prepare import prepare_jobs
from autoapp.steps.send import TEST_PREFIX, build_test_copy, load_message, send_applications, send_test_email
from tests.helpers import make_workspace
from tests.steps.test_modes import FakeDraftStore
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

OWN = "max.mustermann@example.com"


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path), mode="automatic")
    yield c
    c.close()


def _prepared(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    return jid


def _db_state(ctx, jid):
    return (
        ctx.repo.get_job(jid)["status"],
        ctx.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0],
        ctx.conn.execute("SELECT COUNT(*) FROM status_events WHERE to_status = 'sent'").fetchone()[0],
    )


def test_one_email_to_me_with_banner_prefix_and_real_content(ctx):
    from pathlib import Path

    jid = _prepared(ctx)
    original = load_message(Path(ctx.repo.get_application(jid)["folder"]))
    mailer = FakeMailer()
    r = send_test_email(ctx, jid, mailer=mailer)
    assert r.error is None and r.kind == "application" and r.original_to == "jobs@muster-gmbh.de"
    assert r.evidence and "jobs@muster-gmbh.de" in r.evidence and r.warning is None
    (msg,) = mailer.sent  # exactly one email
    assert mailer.envelopes == [[OWN]]
    assert msg["To"] == OWN and msg["Cc"] is None and msg["Bcc"] is None and msg["Reply-To"] is None
    assert msg["Subject"] == "TEST_RUN: " + original["Subject"]
    assert msg["From"] == original["From"] and msg["X-Autoapp-Test"] == "1"
    assert msg["Message-ID"] != original["Message-ID"]
    body = msg.get_body(("plain",)).get_content()
    first, _, rest = body.partition("\n\n")
    assert first == "This email is what would be sent to jobs@muster-gmbh.de in a real run."
    assert rest.strip() == original.get_body(("plain",)).get_content().strip()  # the letter is untouched
    assert "muster-gmbh" not in str(msg["To"])
    assert [a.get_filename() for a in msg.iter_attachments()] == ["Bewerbungsmappe.pdf"]
    assert next(msg.iter_attachments()).get_content() == next(original.iter_attachments()).get_content()


def test_a_missing_address_in_the_ad_is_warned_about(ctx):
    jid = _prepared(ctx)
    ctx.repo.update_job(jid, description="Bewerben Sie sich bitte schriftlich.")
    mailer = FakeMailer()
    r = send_test_email(ctx, jid, mailer=mailer)
    assert r.evidence is None and "not found word for word" in (r.warning or "")
    assert len(mailer.sent) == 1


def test_mismatch_between_eml_and_application_is_refused(ctx):
    jid = _prepared(ctx)
    ctx.repo.upsert_application(jid, contact_email="other@muster-gmbh.de")
    mailer = FakeMailer()
    r = send_test_email(ctx, jid, mailer=mailer)
    assert "addressed to jobs@muster-gmbh.de" in (r.error or "") and mailer.sent == []


def test_original_email_file_is_not_modified(ctx):
    from pathlib import Path

    jid = _prepared(ctx)
    path = Path(ctx.repo.get_application(jid)["folder"]) / "email.eml"
    before = path.read_bytes()
    send_test_email(ctx, jid, mailer=FakeMailer())
    assert path.read_bytes() == before


def test_review_mode_job_is_test_sent_from_the_stored_letter(ctx):
    ctx.mode = "review"
    jid = _prepared(ctx)
    mailer = FakeMailer()
    r = send_test_email(ctx, jid, mailer=mailer, renderer_factory=factory(FakeRenderer([1])))
    assert r.error is None and r.kind == "application" and r.original_to == "jobs@muster-gmbh.de"
    (msg,) = mailer.sent
    assert msg["To"] == OWN
    assert [a.get_filename() for a in msg.iter_attachments()] == ["Bewerbungsmappe.pdf"]
    assert list(ctx.ws.applications_dir.iterdir()) == []


def test_manual_send_of_a_drafted_folderless_job(ctx):
    ctx.mode = "review"
    jid = _prepared(ctx)
    draft_applications(ctx, store=FakeDraftStore(), renderer_factory=factory(FakeRenderer([1])))
    assert ctx.repo.get_job(jid)["status"] == "drafted"
    mailer = FakeMailer()
    s = send_applications(ctx, [jid], manual=True, mailer=mailer, confirm=lambda *_: True,
                          renderer_factory=factory(FakeRenderer([1])))
    assert s.sent == [jid] and ctx.repo.get_job(jid)["status"] == "sent"
    assert mailer.sent[0]["To"] == "jobs@muster-gmbh.de"
    assert list(ctx.ws.applications_dir.iterdir()) == []


def test_nothing_is_recorded_and_the_job_can_still_be_sent(ctx):
    ctx.mode = "automatic"
    jid = _prepared(ctx)
    before = _db_state(ctx, jid)
    send_test_email(ctx, jid, mailer=FakeMailer())
    send_test_email(ctx, jid, mailer=FakeMailer())  # repeatable
    assert _db_state(ctx, jid) == before == ("prepared", 0, 0)
    real = FakeMailer()
    s = send_applications(ctx, [jid], mailer=real)
    assert s.sent == [jid] and real.sent[0]["To"] == "jobs@muster-gmbh.de"


def test_defaults_to_the_newest_prepared_email_job(ctx):
    jid = _prepared(ctx)
    mailer = FakeMailer()
    r = send_test_email(ctx, mailer=mailer)
    assert r.job_id == jid and len(mailer.sent) == 1


def test_without_any_prepared_job_a_plain_test_mail_is_sent(ctx):
    mailer = FakeMailer()
    r = send_test_email(ctx, mailer=mailer)
    assert r.kind == "connection" and r.job_id is None and r.error is None
    (msg,) = mailer.sent
    assert msg["To"] == msg["From"] == OWN and msg["Subject"].startswith(TEST_PREFIX)
    assert list(msg.iter_attachments()) == []


def test_unknown_job_and_job_without_email_file(ctx):
    mailer = FakeMailer()
    assert "unknown job" in (send_test_email(ctx, "nope", mailer=mailer).error or "")
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH")
    assert "no stored email" in (send_test_email(ctx, jid, mailer=mailer).error or "")
    assert mailer.sent == []


def test_no_sending_mailbox_is_reported(ctx):
    ctx.config.mail.sending = None
    r = send_test_email(ctx, mailer=FakeMailer())
    assert "no sending mailbox" in (r.error or "")


def test_login_failure_and_missing_copy_are_reported(ctx):
    bad = send_test_email(ctx, mailer=FakeMailer(MailAuthError("rejected")))
    assert bad.error == "rejected"
    warn = send_test_email(ctx, mailer=FakeMailer(MailSentNoCopyError("no Sent folder")))
    assert warn.error is None and "no Sent folder" in (warn.warning or "")


def test_missing_password_is_reported_without_a_mailer(ctx, monkeypatch):
    monkeypatch.delenv("AUTOAPP_SMTP_PASSWORD", raising=False)
    assert "is not set" in (send_test_email(ctx).error or "")


def test_build_test_copy_drops_every_other_recipient():
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Cc"], m["Bcc"], m["Reply-To"] = OWN, "hr@firma.de", "x@a.de", "y@b.de", "z@c.de"
    m["Subject"], m["Message-ID"] = "Bewerbung", "<a@b>"
    m.set_content("Hallo")
    out = build_test_copy(m, OWN)
    assert [out[h] for h in ("To", "Cc", "Bcc", "Reply-To")] == [OWN, None, None, None]
    assert out["Subject"] == "TEST_RUN: Bewerbung"
    assert out.get_content().startswith("This email is what would be sent to hr@firma.de in a real run.\n\nHallo")
    assert m["To"] == "hr@firma.de" and m["Subject"] == "Bewerbung"  # the original object is untouched
