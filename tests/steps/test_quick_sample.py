"""The quick test's sample limits (run-modes spec): each step takes only a small sample, normal runs are unchanged."""

from __future__ import annotations

import pytest

from autoapp.db.status import Status
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.steps.context import RunContext, SampleLimits
from autoapp.steps.prepare import _sample_jobs, prepare_jobs, select_sample_jobs
from autoapp.steps.search import run_search
from autoapp.steps.send import send_test_applications
from tests.alerts.conftest import account_mail
from tests.helpers import make_workspace
from tests.steps.test_other_steps import FakeResolver, FakeSource, _eml
from tests.steps.test_prepare_step import AD, LETTER, FakeRenderer, evaluation_payload, factory


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def test_a_normal_run_has_no_sample(ctx):
    assert ctx.sample is None


def test_the_sample_lowers_only_the_per_run_limits(ctx):
    ctx.config.limits.max_evaluate_per_run, ctx.config.limits.max_prepare_per_run = 200, 50
    SampleLimits().apply_to(ctx.config)
    assert (ctx.config.limits.max_evaluate_per_run, ctx.config.limits.max_prepare_per_run) == (6, 4)
    ctx.config.limits.max_evaluate_per_run = 1
    SampleLimits().apply_to(ctx.config)
    assert ctx.config.limits.max_evaluate_per_run == 1  # never raised


def test_the_sample_reads_only_a_few_arbeitnow_pages(ctx):
    assert ctx.config.sources.arbeitnow.max_pages_per_run == 40
    SampleLimits().apply_to(ctx.config)
    assert ctx.config.sources.arbeitnow.max_pages_per_run == 3 == ctx.config.sources.arbeitnow.max_requests_per_run


def _account_mails(count=2):
    return [(f"<acc{i}>", account_mail("Workwise <noreply@workwise.io>",
                                       "Confirm your email: https://www.workwise.io/confirm?token=x\n", f"<acc{i}>"))
            for i in range(count)]


def test_the_newest_alert_of_each_sender_is_read_and_newer_account_mail_is_ignored(ctx):
    ctx.sample = SampleLimits()
    ctx.clients["alert_extract"] = llm = FakeLLM({"alert_extract": [{"jobs": []}]})
    mbox = FakeMailbox([("<old>", _eml("indeed_match.eml")), ("<l1>", _eml("linkedin_alert.eml")),
                        ("<s1>", _eml("stepstone_alert.eml")), *_account_mails()])
    s = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert s.alert_emails == 3 and set(s.alert_parsers) == {"indeed", "linkedin", "stepstone"}  # no workwise entry
    assert all(cards for cards in s.alert_parsers.values()) and s.alert_cards > 0
    assert len(llm.calls_for("alert_extract")) == 1 and s.warnings == []
    ids = {r[0] for r in ctx.conn.execute("SELECT message_id FROM processed_emails")}
    assert ids == {"<old>", "<l1>", "<s1>"}


def test_one_email_per_sender_even_when_a_sender_has_several_alerts(ctx):
    ctx.sample = SampleLimits()
    mbox = FakeMailbox([("<l0>", _eml("linkedin_alert.eml")), ("<l1>", _eml("linkedin_alert.eml")),
                        ("<s1>", _eml("stepstone_alert.eml"))])
    s = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert s.alert_emails == 2
    assert {r[0] for r in ctx.conn.execute("SELECT message_id FROM processed_emails")} == {"<l1>", "<s1>"}


def test_the_sample_only_looks_at_the_scanned_window_of_recent_messages(ctx):
    ctx.sample = SampleLimits(alert_scan=2)
    mbox = FakeMailbox([("<l1>", _eml("linkedin_alert.eml")), *_account_mails()])
    s = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert s.alert_emails == 0 and s.alert_parsers == {}  # the only alert is older than the window


def test_a_normal_run_marks_account_mail_processed_without_a_fallback_call_or_warning(ctx):
    ctx.clients["alert_extract"] = llm = FakeLLM({"alert_extract": [{"jobs": []}]})
    mbox = FakeMailbox([("<l1>", _eml("linkedin_alert.eml")), *_account_mails()])
    s = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert s.alert_emails == 1 and s.alert_not_alerts == 2 and s.warnings == [] and s.alert_unprocessed == 0
    [(_, _, user)] = llm.calls_for("alert_extract")
    assert "Confirm your email" not in user and "workwise.io/confirm" not in user
    assert "workwise" not in s.alert_parsers
    rows = {r[0]: r[1] for r in ctx.conn.execute("SELECT message_id, cards FROM processed_emails")}
    assert rows["<acc0>"] == 0 and rows["<acc1>"] == 0 and rows["<l1>"] > 0


def test_without_a_sample_all_alert_emails_are_read(ctx):
    mbox = FakeMailbox([("<old>", _eml("indeed_match.eml")), ("<l1>", _eml("linkedin_alert.eml")),
                        ("<s1>", _eml("stepstone_alert.eml"))])
    assert run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver()).alert_emails == 3


def test_each_source_keeps_a_few_jobs_and_asks_one_profile(ctx):
    ctx.sample = SampleLimits(jobs_per_source=2)
    jobs = [RawJob(source="fakeapi", source_job_id=str(i), title=f"Backend Developer {i}", company=f"C{i} GmbH",
                   location="Berlin", description=AD) for i in range(6)]
    source = FakeSource(jobs)
    s = run_search(ctx, api_sources=[source], mailbox=FakeMailbox([]))
    assert s.sources["fakeapi"].new == 2 and len(ctx.repo.jobs_by_status("new")) == 2


def _evaluated(ctx, company, score, channel, status, source=None):
    jid = ctx.repo.insert_job(title="Backend Developer (m/w/d)", company=company, location="Berlin", description=AD,
                              job_url="https://jobs.example.de/" + company.replace(" ", "-"))
    payload = evaluation_payload(ctx).model_dump(mode="json")
    payload.update(fit_score=score, channel=channel)  # the writer's checks use the evaluation's own bridgeable gaps
    ctx.repo.add_evaluation(jid, fit_score=score, decision=payload["decision"], payload=payload)
    ctx.repo.transition(jid, status)
    if source:
        ctx.repo.add_source(jid, source, source_job_id=jid)
    return jid


def test_prepare_takes_the_best_email_and_the_best_form_job_below_the_threshold(ctx):
    ctx.sample = SampleLimits(email_applications=1, form_applications=1)
    ctx.config.limits.max_prepare_per_run = 2
    low_email = _evaluated(ctx, "Firma EmailLow", 40, "email", "skipped")
    best_form = _evaluated(ctx, "Firma FormMid", 55, "employer_form", "review")
    _evaluated(ctx, "Firma EmailLowest", 20, "email", "skipped")
    _evaluated(ctx, "Firma FormHigh", 59, "employer_form", "review")
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 2})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert s.prepared == 2 and set(s.prepared_ids) <= {low_email, best_form} | {
        j["id"] for j in ctx.repo.jobs_by_status("prepared")}
    assert ctx.repo.get_job(low_email)["status"] == "prepared"
    prepared = {j["company"] for j in ctx.repo.jobs_by_status("prepared")}
    assert prepared == {"Firma EmailLow", "Firma FormHigh"}  # the best of each kind, not the lowest scores
    note = ctx.conn.execute("SELECT note FROM status_events WHERE job_id = ? AND to_status = 'approved'",
                            (low_email,)).fetchone()[0]
    assert "quick test" in note and "fit score 40" in note


def test_without_a_sample_prepare_only_takes_approved_jobs(ctx):
    _evaluated(ctx, "Firma EmailLow", 40, "email", "skipped")
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert s.prepared == 0 and ctx.repo.jobs_by_status("prepared") == []


def test_the_quick_test_sends_at_most_one_test_email_whatever_the_score(ctx):
    ctx.sample = SampleLimits(test_emails=1)
    ctx.config.limits.max_prepare_per_run = 2
    _evaluated(ctx, "Firma One", 40, "email", "skipped")
    _evaluated(ctx, "Firma Two", 35, "email", "skipped")
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 2})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    mailer = FakeMailer()
    result = send_test_applications(ctx, mailer=mailer)
    assert len(result.sent) == 1 and len(mailer.sent) == 1 and result.not_sent == {}
    assert mailer.envelopes == [["max.mustermann@example.com"]]  # only to myself
    assert all(j["status"] == Status.PREPARED.value for j in ctx.repo.jobs_by_status("prepared"))


def test_the_sample_limits_add_up_and_cover_three_form_jobs():
    limits = SampleLimits()
    assert (limits.email_applications, limits.form_applications, limits.preparations, limits.evaluations) == (1, 3, 4, 6)


def test_prepare_takes_form_jobs_from_arbeitsagentur_an_alert_and_another_source(ctx):
    ctx.sample = SampleLimits()
    ctx.config.limits.max_prepare_per_run = 4
    email = _evaluated(ctx, "Firma Mail", 50, "email", "skipped", "adzuna")
    top_other = _evaluated(ctx, "Firma OtherTop", 90, "employer_form", "review", "adzuna")
    _evaluated(ctx, "Firma OtherTop2", 89, "employer_form", "review", "workwise")
    arbeit = _evaluated(ctx, "Firma Arbeit", 30, "employer_form", "review", "arbeitsagentur")
    alert = _evaluated(ctx, "Firma Alert", 20, "platform", "skipped", "linkedin-alert")
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 4})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1] * 4)))
    assert set(s.prepared_ids) == {email, top_other, arbeit, alert}  # one per source, not the three best scores


def test_a_source_without_a_candidate_gives_its_slot_to_the_next_best_score(ctx):
    ctx.sample = SampleLimits()
    ctx.config.limits.max_prepare_per_run = 4
    forms = [_evaluated(ctx, f"Firma F{i}", 80 - i, "employer_form", "review", "adzuna") for i in range(4)]
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 4})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1] * 4)))
    assert set(s.prepared_ids) == set(forms)  # no email, Arbeitsagentur or alert job: every free slot goes to the next best score


def _below_threshold_arbeitsagentur(ctx, company="QIAGEN N.V."):
    jid = _evaluated(ctx, company, 30, "employer_form", "skipped", "arbeitsagentur")
    ctx.repo.update_job(jid, job_url="https://www.arbeitsagentur.de/jobsuche/jobdetail/1")
    return jid


def test_a_below_threshold_sample_job_gets_the_own_ad_search_once_it_is_selected(ctx):
    from autoapp.employers.service import find_own_ads
    from tests.employers.identity_world import search_client

    ctx.sample = SampleLimits()
    jid = _below_threshold_arbeitsagentur(ctx)
    api, calls = search_client({"Find the job ad": []})
    assert find_own_ads(ctx, fetch=lambda url: None, api=api).searched == 0  # skipped jobs are not searched
    jobs = select_sample_jobs(ctx)
    assert [j["id"] for j in jobs] == [jid] and ctx.sample_job_ids == [jid]
    assert ctx.repo.get_job(jid)["status"] == "approved"
    api, calls = search_client({"Find the job ad": []})
    assert find_own_ads(ctx, fetch=lambda url: None, api=api).searched == 1
    assert any(c["objective"].startswith("Find the job ad") for c in calls)
    assert [j["id"] for j in _sample_jobs(ctx)] == [jid]  # prepare takes exactly the selected sample


def test_the_selected_jobs_come_first_within_the_employer_lookups(ctx):
    from autoapp.employers.service import find_own_ads
    from tests.employers.identity_world import search_client

    ctx.sample = SampleLimits(employer_lookups=1)
    _evaluated(ctx, "Firma Older", 99, "employer_form", "approved", "arbeitsagentur")
    chosen = _below_threshold_arbeitsagentur(ctx)
    ctx.repo.transition(chosen, "approved")
    ctx.sample_job_ids = [chosen]
    api, calls = search_client({"Find the job ad": []})
    assert find_own_ads(ctx, fetch=lambda url: None, api=api).searched == 1
    assert "QIAGEN" in str(calls) and "Firma Older" not in str(calls)  # the older job lost the single lookup


def test_the_pipeline_selects_the_quick_test_sample_before_the_own_ad_search(ctx, monkeypatch):
    from autoapp.employers.service import OwnAdsSummary
    from autoapp.steps import pipeline

    ctx.sample = SampleLimits()
    ctx.mode = "test"
    jid = _below_threshold_arbeitsagentur(ctx)
    seen = {}

    def own_ads(c, **kw):
        seen["own_ads"] = (list(c.sample_job_ids or []), c.repo.get_job(jid)["status"])
        return OwnAdsSummary()

    def prepare(c, **kw):
        seen["prepare"] = [j["id"] for j in _sample_jobs(c)]
        from autoapp.steps.prepare import PrepareSummary
        return PrepareSummary()

    monkeypatch.setattr(pipeline, "find_own_ads", own_ads)
    monkeypatch.setattr(pipeline, "own_ads_wanted", lambda config: True)
    monkeypatch.setattr(pipeline, "prepare_jobs", prepare)
    pipeline.run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([]))
    assert seen["own_ads"] == ([jid], "approved") and seen["prepare"] == [jid]
