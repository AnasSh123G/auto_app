"""The quick test's sample limits (run-modes spec): each step takes only a small sample, normal runs are unchanged."""

from __future__ import annotations

import pytest

from autoapp.db.status import Status
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.steps.context import RunContext, SampleLimits
from autoapp.steps.prepare import prepare_jobs
from autoapp.steps.search import run_search
from autoapp.steps.send import send_test_applications
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
    assert (ctx.config.limits.max_evaluate_per_run, ctx.config.limits.max_prepare_per_run) == (4, 2)
    ctx.config.limits.max_evaluate_per_run = 1
    SampleLimits().apply_to(ctx.config)
    assert ctx.config.limits.max_evaluate_per_run == 1  # never raised


def test_the_sample_reads_only_a_few_arbeitnow_pages(ctx):
    assert ctx.config.sources.arbeitnow.max_pages_per_run == 40
    SampleLimits().apply_to(ctx.config)
    assert ctx.config.sources.arbeitnow.max_pages_per_run == 3 == ctx.config.sources.arbeitnow.max_requests_per_run


def test_only_the_newest_alert_emails_are_read_and_parsers_are_named(ctx):
    ctx.sample = SampleLimits(alert_emails=2)
    mbox = FakeMailbox([("<old>", _eml("indeed_match.eml")), ("<l1>", _eml("linkedin_alert.eml")),
                        ("<s1>", _eml("stepstone_alert.eml"))])
    s = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert s.alert_emails == 2 and set(s.alert_parsers) == {"linkedin", "stepstone"}
    ids = {r[0] for r in ctx.conn.execute("SELECT message_id FROM processed_emails")}
    assert ids == {"<l1>", "<s1>"}  # the oldest mail was never looked at


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


def _evaluated(ctx, company, score, channel, status):
    jid = ctx.repo.insert_job(title="Backend Developer (m/w/d)", company=company, location="Berlin", description=AD,
                              job_url="https://jobs.example.de/" + company.replace(" ", "-"))
    payload = evaluation_payload(ctx).model_dump(mode="json")
    payload.update(fit_score=score, channel=channel)  # the writer's checks use the evaluation's own bridgeable gaps
    ctx.repo.add_evaluation(jid, fit_score=score, decision=payload["decision"], payload=payload)
    ctx.repo.transition(jid, status)
    return jid


def test_prepare_takes_the_best_email_and_the_best_form_job_below_the_threshold(ctx):
    ctx.sample = SampleLimits(preparations=2)
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
