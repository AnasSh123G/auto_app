"""Source-scoped runs never process cached jobs from an excluded source."""

from unittest.mock import Mock

import pytest

from autoapp.db.status import Status
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext, SampleLimits
from autoapp.steps.drafts import check_drafts, draft_applications, draftable_jobs
from autoapp.steps.evaluate import evaluate_jobs
from autoapp.steps.fetch import fetch_details
from autoapp.steps.prepare import _sample_jobs, prepare_jobs, select_sample_jobs
from autoapp.steps.send import send_applications, send_test_applications, send_test_email
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import add, verdict
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory


@pytest.fixture
def ctx(tmp_path):
    context = RunContext.open(make_workspace(tmp_path))
    yield context
    context.close()


def scope(ctx, allowed):
    ctx.source_mode = "email_only"
    # Isolate step queue selection from the provenance classifier's own tests.
    ctx.jobs_for_sources = lambda rows: [row for row in rows if row["id"] in allowed]


@pytest.mark.parametrize("explicit", [False, True])
def test_evaluation_filters_before_limit(ctx, explicit):
    excluded, included = [add(ctx, title=title) for title in ("Excluded", "Included")]
    scope(ctx, {included})
    ctx.config.limits.max_evaluate_per_run = 1
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)]})
    result = evaluate_jobs(ctx, [excluded, included] if explicit else None)
    assert result.evaluated == 1 and result.waiting == 0
    assert ctx.repo.get_job(included)["status"] == "approved"
    assert ctx.repo.get_job(excluded)["status"] == "new"


@pytest.mark.parametrize("explicit", [False, True])
def test_preparation_filters_before_limit(ctx, explicit):
    excluded, included = [approved_job(ctx, title=title) for title in ("Excluded", "Included")]
    scope(ctx, {included})
    ctx.config.limits.max_prepare_per_run = 1
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    result = prepare_jobs(ctx, [excluded, included] if explicit else None,
                          renderer_factory=factory(FakeRenderer([1])))
    assert result.prepared_ids == [included] and result.waiting == 0
    assert ctx.repo.get_job(excluded)["status"] == "approved"


def test_details_do_not_request_excluded_pages(ctx):
    excluded = add(ctx)
    ctx.repo.update_job(excluded, description="")
    scope(ctx, set())
    http = Mock()
    result = fetch_details(ctx, http=http)
    assert result.fetched == result.unavailable == 0
    http.fetch.assert_not_called()
    assert ctx.repo.get_job(excluded)["status"] == "new"


def test_sample_selection_and_cached_sample_respect_scope(ctx):
    excluded, included = [approved_job(ctx, title=title) for title in ("Excluded", "Included")]
    scope(ctx, {included})
    ctx.sample = SampleLimits(email_applications=1, form_applications=0)
    assert [job["id"] for job in select_sample_jobs(ctx)] == [included]
    ctx.sample_job_ids = [excluded, included]
    assert [job["id"] for job in _sample_jobs(ctx)] == [included]


def prepared(ctx):
    job_id = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    return job_id


@pytest.mark.parametrize("explicit", [False, True])
def test_cached_prepared_jobs_are_not_sent_or_drafted(ctx, explicit):
    job_id = prepared(ctx)
    scope(ctx, set())
    ctx.dry_run = True
    ids = [job_id] if explicit else None
    assert draftable_jobs(ctx) == []
    assert send_applications(ctx, ids).would_send == []
    assert send_test_applications(ctx).would_send == {}
    assert draft_applications(ctx, ids).would_save == []


def test_explicit_test_email_cannot_use_excluded_job(ctx):
    job_id = prepared(ctx)
    scope(ctx, set())
    mailer = Mock()
    result = send_test_email(ctx, job_id, mailer=mailer)
    assert result.error and "source mode" in result.error
    mailer.send.assert_not_called()


def test_draft_check_never_opens_mailbox_for_excluded_jobs(ctx):
    job_id = prepared(ctx)
    ctx.repo.transition(job_id, Status.DRAFTED)
    scope(ctx, set())
    store_factory = Mock(side_effect=AssertionError("excluded job opened mailbox"))
    assert check_drafts(ctx, store_factory=store_factory).sent == []
    store_factory.assert_not_called()


@pytest.mark.parametrize("operation", ["run_discovery", "find_alert_ads", "find_own_ads"])
def test_employer_search_filters_before_network(ctx, monkeypatch, operation):
    from autoapp.employers import service

    job_id = add(ctx)
    scope(ctx, set())
    ctx.config.sources.employer_careers.enabled = True
    monkeypatch.setattr(service, "eligible_seed_rows", lambda *args, **kwargs: [ctx.repo.get_job(job_id)])
    network = Mock(side_effect=AssertionError("excluded job opened network"))
    monkeypatch.setattr(service, "make_fetch", network)
    getattr(service, operation)(ctx, api=Mock())
    network.assert_not_called()


def test_email_only_employer_enrichment_never_imports_related_jobs(ctx, monkeypatch):
    from autoapp.employers import service

    job_id = add(ctx)
    scope(ctx, {job_id})
    ctx.config.sources.employer_careers.enabled = True
    monkeypatch.setattr(service, "eligible_seed_rows", lambda *args, **kwargs: [ctx.repo.get_job(job_id)])
    process = Mock()
    monkeypatch.setattr(service, "_process_group", process)
    service.run_discovery(ctx, fetch=Mock(), api=Mock(), import_related=True)
    assert process.call_args.kwargs["import_related"] is False


@pytest.mark.parametrize("source_mode", ["email_only", "full"])
def test_pending_reconciliation_keeps_excluded_web_jobs_unchanged(ctx, source_mode):
    email_job, web_job = [add(ctx, title=title) for title in ("Email", "Web")]
    ctx.repo.add_source(email_job, "xing-alert", "email-pending")
    ctx.repo.add_source(web_job, "adzuna", "web-pending")
    for job_id in (email_job, web_job):
        ctx.repo.transition(job_id, Status.PREPARED)
        ctx.conn.execute(
            "INSERT INTO send_attempts (job_id, message_id, state, recipient, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?)",
            (job_id, f"<{job_id}@example.com>", "jobs@example.com",
             "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )
    ctx.conn.commit()
    ctx.source_mode = source_mode
    result = send_applications(ctx, [])
    expected = {email_job, web_job} if source_mode == "full" else {email_job}
    assert set(result.unconfirmed) == expected
    assert ctx.repo.get_job(email_job)["status"] == "send_unconfirmed"
    assert ctx.repo.get_job(web_job)["status"] == ("send_unconfirmed" if source_mode == "full" else "prepared")
