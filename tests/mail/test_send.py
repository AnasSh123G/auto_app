from datetime import UTC, datetime
from email.message import EmailMessage

import pytest
from freezegun import freeze_time

from autoapp.config.models import EmailPolicy
from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.db.status import Status
from autoapp.mail.connection import FakeMailer, MailAuthError, MailSendError, MailSentNoCopyError
from autoapp.mail.send import check_eligibility, reconcile_pending, send_application


@pytest.fixture
def repo(tmp_path):
    return Repo(open_db(tmp_path / "t.db"))


@pytest.fixture
def bundle(tmp_path):
    p = tmp_path / "Bewerbungsmappe.pdf"
    p.write_bytes(b"%PDF-1.4 " + b"x" * 1000)
    return p


def prepared(repo, company="Muster GmbH", email="jobs@muster-gmbh.de", score=90, decision="apply"):
    jid = repo.insert_job(title="Dev", company=company)
    repo.add_evaluation(jid, fit_score=score, decision=decision)
    repo.upsert_application(jid, channel="email", contact_email=email)
    repo.transition(jid, Status.PREPARED)
    return jid


POLICY = EmailPolicy()


def msg(n=1):
    m = EmailMessage()
    m["To"] = "jobs@muster-gmbh.de"
    m["Message-ID"] = f"<test-{n}@example.com>"
    m.set_content("x")
    return m


def test_eligible(repo, bundle):
    e = check_eligibility(repo, prepared(repo), POLICY, bundle=bundle)
    assert e.ok, e.reasons


@pytest.mark.parametrize(
    ("setup", "policy", "expect"),
    [
        (lambda r: prepared(r, decision="maybe"), POLICY, "decision is not apply"),
        (lambda r: prepared(r, email="muster.jobs@gmail.com"), POLICY, "free-mail"),
        (lambda r: prepared(r, score=80), EmailPolicy(min_score=85), "below email.min_score"),
    ],
)
def test_rejections(repo, bundle, setup, policy, expect):
    e = check_eligibility(repo, setup(repo), policy, bundle=bundle)
    assert not e.ok and any(expect in r for r in e.reasons)


def test_free_mail_flags_review(repo, bundle):
    e = check_eligibility(repo, prepared(repo, email="x@gmx.de"), POLICY, bundle=bundle)
    assert e.flag_review


def test_wildcard_free_mail_domains(repo, bundle):
    e = check_eligibility(repo, prepared(repo, email="hr@yahoo.co.uk"), POLICY, bundle=bundle)
    assert any("free-mail" in r for r in e.reasons)


def test_channel_not_email(repo, bundle):
    jid = prepared(repo)
    repo.upsert_application(jid, channel="employer_form")
    assert "does not take applications by email" in " ".join(check_eligibility(repo, jid, POLICY, bundle=bundle).reasons)


def test_needs_review_blocks(repo, bundle):
    jid = prepared(repo)
    repo.set_needs_review(jid, ["unsupported claim: 5 Jahre AWS"])
    assert "5 Jahre AWS" in " ".join(check_eligibility(repo, jid, POLICY, bundle=bundle).reasons)


def test_missing_or_large_bundle(repo, tmp_path, bundle):
    jid = prepared(repo)
    assert not check_eligibility(repo, jid, POLICY, bundle=tmp_path / "nope.pdf").ok
    tiny = EmailPolicy(max_bundle_mb=0.0001)
    assert any("larger than" in r for r in check_eligibility(repo, jid, tiny, bundle=bundle).reasons)


def test_manual_send_skips_auto_switch_and_score(repo, bundle):
    jid = prepared(repo, score=50)
    assert check_eligibility(repo, jid, EmailPolicy(), bundle=bundle, manual=True).ok


# --- 9.3 limits -----------------------------------------------------------------------


@freeze_time("2026-10-08 10:00:00")
def test_daily_cap(repo, bundle):
    policy = EmailPolicy(daily_cap=5)
    for i in range(5):
        jid = prepared(repo, company=f"Firma {i}")
        assert send_application(repo, jid, msg(i), FakeMailer()).sent
    nxt = prepared(repo, company="Firma 99")
    e = check_eligibility(repo, nxt, policy, bundle=bundle)
    assert any("daily cap of 5" in r for r in e.reasons)


def test_cap_resets_on_next_berlin_day(repo, bundle):
    policy = EmailPolicy(daily_cap=1)
    with freeze_time("2026-10-08 21:30:00"):  # 23:30 in Berlin
        send_application(repo, prepared(repo, company="A"), msg(1), FakeMailer())
        assert not check_eligibility(repo, prepared(repo, company="B"), policy, bundle=bundle).ok
    with freeze_time("2026-10-08 22:30:00"):  # 00:30 next day in Berlin
        assert check_eligibility(repo, repo.jobs_by_status("prepared")[0]["id"], policy, bundle=bundle).ok


def test_duplicate_send_blocked(repo, bundle):
    jid = prepared(repo)
    send_application(repo, jid, msg(), FakeMailer())
    repo.transition(jid, Status.PREPARED)  # even if someone resets the status
    assert any("already sent" in r for r in check_eligibility(repo, jid, POLICY, bundle=bundle).reasons)


def test_company_cooldown(repo, bundle):
    with freeze_time("2026-10-01 10:00:00"):
        send_application(repo, prepared(repo, company="Muster GmbH"), msg(1), FakeMailer())
    with freeze_time("2026-10-04 10:00:00"):
        e = check_eligibility(repo, prepared(repo, company="Muster GmbH"), POLICY, bundle=bundle,
                              now=datetime.now(UTC))
        assert any("3 days ago" in r and "cooldown 14" in r for r in e.reasons) and e.flag_review
    with freeze_time("2026-10-16 10:00:00"):
        job = repo.jobs_by_status("prepared")[0]["id"]
        assert check_eligibility(repo, job, POLICY, bundle=bundle, now=datetime.now(UTC)).ok


# --- 9.4 idempotency / 9.5 failures ------------------------------------------------------


def test_successful_send_records_everything(repo):
    jid = prepared(repo)
    r = send_application(repo, jid, msg(), FakeMailer())
    assert r.sent and r.message_id == "<test-1@example.com>"
    assert repo.get_job(jid)["status"] == "sent"
    row = repo.conn.execute("SELECT * FROM send_attempts WHERE job_id = ?", (jid,)).fetchone()
    assert row["state"] == "sent" and row["message_id"] == "<test-1@example.com>"


def test_crash_after_pending_becomes_unconfirmed_and_never_resent(repo, bundle):
    jid = prepared(repo)

    class Crash(Exception):
        pass

    class CrashingMailer(FakeMailer):
        def send(self, m):
            raise Crash()

    with freeze_time("2026-10-08 10:00:00"), pytest.raises(Crash):
        send_application(repo, jid, msg(), CrashingMailer())
    with freeze_time("2026-10-08 10:20:00"):
        assert reconcile_pending(repo, datetime.now(UTC)) == [jid]
        assert repo.get_job(jid)["status"] == "send_unconfirmed"
        e = check_eligibility(repo, jid, POLICY, bundle=bundle)
        assert not e.ok and any("unconfirmed" in r for r in e.reasons)


def test_auth_failure_reports_and_keeps_prepared(repo):
    jid = prepared(repo)
    r = send_application(repo, jid, msg(), FakeMailer(MailAuthError("bad password")))
    assert not r.sent and r.auth_failed
    assert repo.get_job(jid)["status"] == "prepared"


def test_other_failure_counts_attempts(repo, bundle):
    jid = prepared(repo)
    for i in range(3):
        assert not send_application(repo, jid, msg(i), FakeMailer(MailSendError("timeout"))).sent
    e = check_eligibility(repo, jid, EmailPolicy(max_attempts=3), bundle=bundle)
    assert any("gave up after 3" in r for r in e.reasons)


def test_sent_without_sent_copy_is_still_sent(repo):
    jid = prepared(repo)
    r = send_application(repo, jid, msg(), FakeMailer(MailSentNoCopyError("no Sent folder")))
    assert r.sent and r.warning
    assert repo.get_job(jid)["status"] == "sent"


def test_message_id_required(repo):
    m = EmailMessage()
    with pytest.raises(ValueError):
        send_application(repo, prepared(repo), m, FakeMailer())


@pytest.mark.parametrize("empty_selection", [[], set()])
def test_pending_reconciliation_empty_selection_changes_nothing(repo, empty_selection):
    jid = prepared(repo)
    repo.conn.execute(
        "INSERT INTO send_attempts (job_id, message_id, state, created_at, updated_at, recipient) "
        "VALUES (?, ?, 'pending', ?, ?, ?)",
        (jid, "<pending@example.com>", "2020-01-01T00:00:00+00:00",
         "2020-01-01T00:00:00+00:00", "jobs@example.com"),
    )
    repo.conn.commit()
    assert reconcile_pending(repo, job_ids=empty_selection) == []
    assert repo.get_job(jid)["status"] == "prepared"
    assert reconcile_pending(repo) == [jid]
