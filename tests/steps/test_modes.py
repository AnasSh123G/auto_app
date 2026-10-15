"""Run modes (add-menu-and-run-modes): drafts (2.x), test run (3.0), branching (3.1), summary (3.2), prompt (3.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoapp.dashboard.wiring import attach_run_events
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer, MailAuthError
from autoapp.menu.run_prompt import choose_run
from autoapp.package.email_message import application_email
from autoapp.setup.prompts import ScriptedPrompter
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.steps.context import RunContext
from autoapp.steps.drafts import check_drafts, draft_applications
from autoapp.steps.pipeline import RunSummary, run_pipeline
from autoapp.steps.prepare import prepare_jobs
from autoapp.steps.send import load_message, send_applications, send_test_applications, send_test_email
from tests.helpers import make_workspace
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

OWN = "max.mustermann@example.com"


class FakeDraftStore:
    def __init__(self, folder="[Gmail]/Entw&APw-rfe", fail=None):
        self.folder, self.fail = folder, fail
        self.drafts: list = []
        self.sent: list[tuple[str, str, datetime]] = []
        self.closed = False

    def drafts_folder(self):
        if isinstance(self.fail, MailAuthError):
            raise self.fail
        return self.folder

    def has_message(self, folder, message_id):
        return any(str(m["Message-ID"]) == message_id for m in self.drafts)

    def append_draft(self, folder, msg):
        self.drafts.append(msg)

    def find_sent(self, recipient, subject, since):
        return any(r == recipient and s == subject and t >= since for r, s, t in self.sent)

    def close(self):
        self.closed = True


class ExplodingMailer(FakeMailer):
    def send(self, msg, **kw):
        raise AssertionError("SMTP send must never be called in this mode")


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def prepared(ctx, n=1, emails=None, scores=None):
    """n prepared email applications in review mode (no folder); optionally retarget recipients and scores."""
    ids = [approved_job(ctx, company=f"Firma {i} GmbH") for i in range(n)]
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * n})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    for i, jid in enumerate(ids):
        if emails:
            ctx.repo.upsert_application(jid, contact_email=emails[i])
        if scores:
            ctx.conn.execute("UPDATE evaluations SET fit_score = ? WHERE job_id = ?", (scores[i], jid))
            ctx.conn.commit()
    return ids


# --- 2.3 drafting -------------------------------------------------------------------------


def test_draft_is_built_from_the_stored_letter(ctx):
    (jid,) = prepared(ctx)
    app = ctx.repo.get_application(jid)
    store = FakeDraftStore()
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [jid] and ctx.repo.get_job(jid)["status"] == "drafted"
    (d,) = store.drafts
    assert (str(d["To"]), str(d["Subject"]), str(d["Message-ID"])) == (app["contact_email"], app["email_subject"], app["message_id"])
    assert d.get_body(("plain",)).get_content().strip() == app["email_body"].strip()
    assert [a.get_filename() for a in d.iter_attachments()] == ["Bewerbungsmappe.pdf"]
    row = ctx.conn.execute("SELECT * FROM drafts WHERE job_id = ?", (jid,)).fetchone()
    assert row["state"] == "saved" and row["recipient"] == "jobs@muster-gmbh.de"
    assert ctx.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 0
    assert list(ctx.ws.applications_dir.iterdir()) == []


def test_free_mail_and_low_score_are_drafted_with_notes(ctx):
    a, b = prepared(ctx, 2, emails=["firma.jobs@gmail.com", "jobs@firma1.de"], scores=[90, 78])
    s = draft_applications(ctx, store=FakeDraftStore(), renderer_factory=factory(FakeRenderer([1])))
    assert sorted(s.saved) == sorted([a, b])
    assert any("free-mail" in n for n in s.notes[a])
    assert any("min_score" in n for n in s.notes[b])


def test_second_run_does_not_duplicate_and_again_replaces(ctx):
    (jid,) = prepared(ctx)
    store = FakeDraftStore()
    draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    s = draft_applications(ctx, [jid], store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.existing == [jid] and len(store.drafts) == 1
    s = draft_applications(ctx, [jid], again=True, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [jid] and len(store.drafts) == 2
    assert store.drafts[0]["Message-ID"] != store.drafts[1]["Message-ID"]
    assert ctx.repo.get_application(jid)["message_id"] == str(store.drafts[1]["Message-ID"])
    states = [r["state"] for r in ctx.conn.execute("SELECT state FROM drafts ORDER BY id")]
    assert states == ["replaced", "saved"]


def test_needs_review_is_drafted_with_a_check_note(ctx):
    (jid,) = prepared(ctx)
    ctx.repo.set_needs_review(jid, ["a claim could not be verified"])
    store = FakeDraftStore()
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [jid] and ctx.repo.get_job(jid)["status"] == "drafted"
    assert s.review[jid] == ["a claim could not be verified"]
    (d,) = store.drafts
    assert "a claim could not be verified" not in d.get_body(("plain",)).get_content()
    assert "check before sending" in "\n".join(RunSummary(drafts=s).lines())


def test_dry_run_saves_nothing(ctx):
    (jid,) = prepared(ctx)
    ctx.dry_run = True
    store = FakeDraftStore()
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.would_save == [jid] and store.drafts == [] and ctx.repo.get_job(jid)["status"] == "prepared"


def test_lost_confirmation_is_recorded_without_a_duplicate(ctx):
    (jid,) = prepared(ctx)
    store = FakeDraftStore()
    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))) as b:
        store.drafts.append(b.message)  # appended earlier, unrecorded
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [jid] and len(store.drafts) == 1


def test_retry_after_failure_needs_no_llm(ctx):
    (jid,) = prepared(ctx)
    s = draft_applications(ctx, store=FakeDraftStore(fail=MailAuthError("rejected")),
                           renderer_factory=factory(FakeRenderer([1])))
    assert s.auth_failed and ctx.repo.get_job(jid)["status"] == "prepared"
    ctx.clients["write"] = FakeLLM(script={"write": []})
    s = draft_applications(ctx, store=FakeDraftStore(), renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [jid]


def test_legacy_folder_is_drafted_then_deleted(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    ctx.mode = "automatic"
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    ctx.mode = "review"
    folder = Path(ctx.repo.get_application(jid)["folder"])
    original = load_message(folder)
    ctx.repo.set_needs_review(jid, ["a claim could not be verified"])
    store = FakeDraftStore()
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [jid]
    (d,) = store.drafts
    app = ctx.repo.get_application(jid)
    assert not folder.exists() and app["folder"] is None and app["letter_md"]
    assert (str(d["To"]), str(d["Subject"]), str(d["Message-ID"])) == (
        str(original["To"]), str(original["Subject"]), str(original["Message-ID"]))
    assert app["email_subject"] == str(original["Subject"]) and app["message_id"] == str(original["Message-ID"])
    assert app["email_body"].strip() == original.get_body(("plain",)).get_content().strip()


def test_legacy_folder_is_kept_when_drafting_fails(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    ctx.mode = "automatic"
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    ctx.mode = "review"
    folder = Path(ctx.repo.get_application(jid)["folder"])
    s = draft_applications(ctx, store=FakeDraftStore(fail=MailAuthError("rejected")),
                           renderer_factory=factory(FakeRenderer([1])))
    assert s.auth_failed and folder.exists() and ctx.repo.get_job(jid)["status"] == "prepared"
    assert ctx.repo.get_application(jid)["folder"] == str(folder)


# --- 2.4 failures ----------------------------------------------------------------------------


def test_login_failure_keeps_jobs_prepared_and_the_run_completes(ctx):
    prepared(ctx, 2)
    s = draft_applications(ctx, store=FakeDraftStore(fail=MailAuthError("the IMAP server rejected the login")),
                           renderer_factory=factory(FakeRenderer([1])))
    assert s.auth_failed and len(s.errors) == 1 and "setup --section email" in s.errors[0]
    assert {j["status"] for j in ctx.repo.jobs_by_status("prepared")} == {"prepared"}
    assert list(ctx.ws.applications_dir.iterdir()) == []


# --- 2.5 sent detection ------------------------------------------------------------------------


def test_check_drafts_marks_sent_reports_missing_and_keeps_edited(ctx):
    a, b, c = prepared(ctx, 3, emails=["a@firma.de", "b@firma.de", "c@firma.de"])
    store = FakeDraftStore()
    draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    subj = {j: str(ctx.repo.get_application(j)["email_subject"]) for j in (a, b, c)}
    later = datetime.now(UTC) + timedelta(minutes=1)
    store.sent.append(("a@firma.de", subj[a], later))  # a: sent by hand
    store.drafts = [d for d in store.drafts if d["To"] != "b@firma.de"]  # b: draft deleted
    store.sent.append(("c@firma.de", "edited subject", later))  # c: subject edited, no match
    s = check_drafts(ctx, store=store)
    assert s.sent == [a] and s.missing == [b] and s.still_drafted == [c]
    assert ctx.repo.get_job(a)["status"] == "sent"
    assert ctx.repo.history(a)[-1]["note"] == "sent by you from the draft"
    assert ctx.repo.get_job(b)["status"] == "drafted" and ctx.repo.get_job(c)["status"] == "drafted"


def test_check_drafts_is_skipped_in_dry_runs(ctx):
    prepared(ctx)
    store = FakeDraftStore()
    draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    ctx.dry_run = True
    assert check_drafts(ctx, store=FakeDraftStore(fail=AssertionError("must not connect"))).sent == []


# --- 3.0 test run ---------------------------------------------------------------------------


def test_test_run_sends_only_to_me_with_the_same_decisions(ctx):
    ids = prepared(ctx, 4, emails=["a@x.de", "b@y.de", "c@z.de", "d@w.de"], scores=[90, 90, 90, 70])
    ctx.config.email.min_score = 85

    class Guard(FakeMailer):
        def send(self, msg, *, to_addrs=None, keep_copy=True):
            assert to_addrs == [OWN] and msg["To"] == OWN and msg["Cc"] is None
            super().send(msg, to_addrs=to_addrs, keep_copy=keep_copy)

    mailer = Guard()
    s = send_test_applications(ctx, mailer=mailer, renderer_factory=factory(FakeRenderer([1])))
    assert sorted(s.sent.values()) == ["a@x.de", "b@y.de", "c@z.de"]
    assert "below email.min_score" in " ".join(s.not_sent[ids[3]])
    for m in mailer.sent:
        assert m["Subject"].startswith("TEST_RUN: ")
        first = m.get_body(("plain",)).get_content().split("\n")[0]
        assert first.startswith("This email is what would be sent to ") and first.endswith(" in a real run.")
    assert {ctx.repo.get_job(j)["status"] for j in ids} == {"prepared"}
    assert ctx.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 0


def test_test_run_simulates_the_daily_cap(ctx):
    ids = prepared(ctx, 3, emails=["a@x.de", "b@y.de", "c@z.de"])
    ctx.config.email.daily_cap = 2
    s = send_test_applications(ctx, mailer=FakeMailer(), renderer_factory=factory(FakeRenderer([1])))
    (held,) = s.not_sent.values()
    assert len(s.sent) == 2 and set(s.sent) | set(s.not_sent) == set(ids) and "daily cap of 2 reached" in " ".join(held)


# --- one job whose email cannot be built does not stop the others --------------------------------


def _break_letter(ctx, job_id):
    ctx.repo.upsert_application(job_id, letter_md="Hallo, kein Anschreiben.")  # no '# <subject>' line


def test_malformed_letter_is_not_drafted_and_the_others_are(ctx):
    bad, good = prepared(ctx, 2)
    _break_letter(ctx, bad)
    store = FakeDraftStore()
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [good] and len(store.drafts) == 1
    assert s.not_drafted[bad] == ["the email could not be built: cover_letter.md must start with '# <subject>'"]
    assert ctx.repo.get_job(bad)["status"] == "prepared"
    assert list(ctx.ws.applications_dir.iterdir()) == []


def test_missing_document_file_is_reported_per_job(ctx):
    ids = prepared(ctx, 2)
    for path in ctx.documents.directory.iterdir():
        path.unlink()
    store = FakeDraftStore()
    s = draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    assert s.saved == [] and store.drafts == [] and set(s.not_drafted) == set(ids)
    for reasons in s.not_drafted.values():
        assert reasons[0].startswith("the email could not be built: ") and "No such file" in reasons[0]
    assert {ctx.repo.get_job(j)["status"] for j in ids} == {"prepared"}


def test_malformed_letter_is_not_sent_and_the_others_are(ctx):
    bad, good = prepared(ctx, 2, emails=["a@x.de", "b@y.de"])
    _break_letter(ctx, bad)
    ctx.mode = "automatic"
    mailer = FakeMailer()
    s = send_applications(ctx, mailer=mailer, renderer_factory=factory(FakeRenderer([1])))
    assert s.sent == [good] and [m["To"] for m in mailer.sent] == ["b@y.de"]
    assert s.not_sent[bad][0].startswith("the email could not be built: ")
    assert ctx.repo.get_job(bad)["status"] == "prepared"


def test_malformed_letter_is_not_test_sent_and_the_others_are(ctx):
    bad, good = prepared(ctx, 2, emails=["a@x.de", "b@y.de"])
    _break_letter(ctx, bad)
    s = send_test_applications(ctx, mailer=FakeMailer(), renderer_factory=factory(FakeRenderer([1])))
    assert s.sent == {good: "b@y.de"} and s.not_sent[bad][0].startswith("the email could not be built: ")


def test_test_email_of_a_malformed_letter_is_an_error(ctx):
    (bad,) = prepared(ctx)
    _break_letter(ctx, bad)
    mailer = FakeMailer()
    r = send_test_email(ctx, bad, mailer=mailer, renderer_factory=factory(FakeRenderer([1])))
    assert r.error.startswith(f"job {bad}: the email could not be built: ") and mailer.sent == []


def test_a_mailbox_error_after_building_still_propagates(ctx):
    prepared(ctx)

    class BrokenStore(FakeDraftStore):
        def append_draft(self, folder, msg):
            raise ConnectionResetError("socket closed")  # an OSError, but not a build error

    with pytest.raises(ConnectionResetError):
        draft_applications(ctx, store=BrokenStore(), renderer_factory=factory(FakeRenderer([1])))


# --- 3.1 / 3.2 pipeline branching and summary ----------------------------------------------------


def _pipeline(ctx, mode, **kw):
    ctx.mode = mode
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": []})
    attach_run_events(ctx)
    return run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([]),
                        renderer_factory=factory(FakeRenderer([1])), **kw)


def test_review_mode_never_calls_smtp(ctx):
    prepared(ctx, 2)
    s = _pipeline(ctx, "review", mailer=ExplodingMailer(), draft_store=FakeDraftStore())
    assert s.ok, s.errors
    assert len(s.drafts.saved) == 2 and s.send is None and s.test is None
    text = "\n".join(s.lines())
    assert "mode: review first" in text and "drafts: 2 saved in your mailbox's [Gmail]/Entwürfe folder" in text
    assert ctx.stats.drafts_saved == 2


def test_review_pipeline_leaves_no_files(ctx):
    prepared(ctx, 3)
    s = _pipeline(ctx, "review", draft_store=FakeDraftStore())
    assert s.ok, s.errors
    assert len(s.drafts.saved) == 3
    assert list(ctx.ws.applications_dir.iterdir()) == []


def test_automatic_mode_sends_and_does_not_draft_ineligible(ctx):
    a, b = prepared(ctx, 2, emails=["a@x.de", "b@y.de"], scores=[90, 70])
    ctx.config.email.min_score = 85
    store = FakeDraftStore()
    mailer = FakeMailer()
    s = _pipeline(ctx, "automatic", mailer=mailer, draft_store=store)
    assert s.send.sent == [a] and b in s.send.not_sent and store.drafts == []
    assert ctx.repo.get_job(b)["status"] == "prepared"


def test_test_mode_in_the_pipeline(ctx):
    prepared(ctx, 1)
    mailer = FakeMailer()
    s = _pipeline(ctx, "test", mailer=mailer)
    assert len(mailer.sent) == 1 and mailer.envelopes == [[OWN]]
    assert "mode: test run" in "\n".join(s.lines()) and "a real run would send to jobs@muster-gmbh.de" in "\n".join(s.lines())


def test_dry_run_review_mode_contacts_no_mailbox(ctx):
    prepared(ctx)
    ctx.dry_run = True
    s = _pipeline(ctx, "review", mailer=ExplodingMailer(), draft_store=FakeDraftStore(fail=AssertionError("no")))
    assert s.drafts.would_save and "would save 1" in "\n".join(s.lines())


def test_draft_login_failure_is_an_error_but_other_steps_complete(ctx):
    prepared(ctx)
    s = _pipeline(ctx, "review", draft_store=FakeDraftStore(fail=MailAuthError("rejected")))
    assert not s.ok and any("draft: rejected" in e for e in s.errors)
    assert ctx.ws.csv_path.exists()


def test_drafts_are_checked_at_the_start_of_a_run(ctx):
    (jid,) = prepared(ctx)
    store = FakeDraftStore()
    draft_applications(ctx, store=store, renderer_factory=factory(FakeRenderer([1])))
    d = store.drafts[0]
    store.sent.append((str(d["To"]), str(d["Subject"]), datetime.now(UTC) + timedelta(minutes=1)))
    s = _pipeline(ctx, "review", draft_store=store)
    assert s.drafts_check.sent == [jid] and "drafts check: 1 sent by you" in "\n".join(s.lines())


@pytest.mark.parametrize("mode", ["automatic", "review", "test"])
def test_forms_are_prepared_the_same_in_every_mode_without_a_browser(ctx, mode, monkeypatch):
    import json

    jid = approved_job(ctx, company="Formular AG")
    row = ctx.conn.execute("SELECT id, payload FROM evaluations WHERE job_id = ?", (jid,)).fetchone()
    payload = json.loads(row["payload"])
    payload["result"].update(channel="employer_form", apply_email=None,
                             application_url="https://jobs.formular.de/apply/1")
    ctx.conn.execute("UPDATE evaluations SET payload = ? WHERE id = ?", (json.dumps(payload), row["id"]))
    ctx.conn.commit()
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})

    def no_browser(*a, **kw):
        raise AssertionError("a run must not open a browser")

    monkeypatch.setattr("autoapp.assist.session.open_browser_context", no_browser)
    s = _pipeline(ctx, mode, mailer=ExplodingMailer(), draft_store=FakeDraftStore())
    assert s.ok, s.errors
    assert s.forms_to_finish == [jid] and ctx.repo.get_job(jid)["status"] == "prepared"
    folder = Path(ctx.repo.get_application(jid)["folder"])
    assert (folder / "apply.html").exists() and (folder / "form_answers.md").exists()
    assert not (folder / "email.eml").exists()
    assert "forms to finish: 1" in "\n".join(s.lines())


# --- 3.3 the prompt -------------------------------------------------------------------------------


def test_choose_review(ctx):
    p = ScriptedPrompter([("How should this run handle emails", "review")])
    assert choose_run(p, ctx.config, 0, ask_sources=False).mode == "review"


def test_confirm_automatic_shows_sent_today(ctx):
    ctx.config.email.daily_cap = 5
    p = ScriptedPrompter([("How should", "automatic"), ("This will email employers", True)])
    assert choose_run(p, ctx.config, 2, ask_sources=False).mode == "automatic"
    assert any("2 of 5 emails sent today" in line for line in p.output)


def test_decline_automatic_asks_again(ctx):
    p = ScriptedPrompter([("How should", "automatic"), ("This will email employers", False), ("How should", "test")])
    assert choose_run(p, ctx.config, 0, ask_sources=False).mode == "test"


def test_default_is_preselected(ctx):
    from autoapp.setup.prompts import ENTER

    ctx.config.run.default_mode = "automatic"
    p = ScriptedPrompter([("How should", ENTER), ("This will email employers", True)])
    assert choose_run(p, ctx.config, 0, ask_sources=False).mode == "automatic"


def test_profile_choice_when_several(ctx):
    from autoapp.config.models import SearchProfile

    ctx.config.search_profiles.append(SearchProfile(name="remote", keywords=["x"]))
    p = ScriptedPrompter([("How should", "review"), ("Which search profiles", "remote")])
    assert choose_run(p, ctx.config, 0, ask_sources=False).profile == "remote"


@pytest.mark.parametrize("source_mode", ["email_only", "full", "web_only"])
def test_choose_sources_before_delivery(ctx, source_mode):
    p = ScriptedPrompter([("Where should", source_mode), ("How should", "review")])
    choice = choose_run(p, ctx.config, 0)
    assert choice.source_mode == source_mode and choice.mode == "review"
    assert p.choice_values[0] == ["email_only", "full", "web_only"]


def test_sources_default_to_full(ctx):
    from autoapp.setup.prompts import ENTER

    p = ScriptedPrompter([("Where should", ENTER), ("How should", "review")])
    assert choose_run(p, ctx.config, 0).source_mode == "full"


def test_explicit_sources_skip_source_prompt(ctx):
    p = ScriptedPrompter([("How should", "review")])
    choice = choose_run(p, ctx.config, 0, ask_sources=False, source_mode="web_only")
    assert choice.source_mode == "web_only" and p.remaining == 0
