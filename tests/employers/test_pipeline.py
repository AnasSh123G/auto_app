"""Pipeline and fetch integration of employer discovery (tasks 5.1, 5.2)."""

from __future__ import annotations

import httpx

from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.steps.context import RunContext
from autoapp.steps.fetch import fetch_details
from autoapp.steps.pipeline import run_pipeline
from tests.employers.identity_world import search_client
from tests.employers.test_service import EMP_CONFIG, TENANT, add_alert, cbc_pages
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import verdict
from tests.steps.test_prepare_step import LETTER, FakeRenderer, factory

EVAL = verdict(90, apply_email=None, requirements=[
    {"text": "Python", "proficiency": "intermediate", "core": True, "status": "matched", "evidence": "Python"}])


def make_ctx(tmp_path, config=EMP_CONFIG, evals=2):
    config = config.replace("max_prepare_per_run: 5", "max_prepare_per_run: 0")  # no form finding over the network here
    ctx = RunContext.open(make_workspace(tmp_path, config=config))
    ctx.mode = "review"
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [EVAL] * evals})
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * evals})
    steps: list[str] = []
    ctx.events.subscribe(lambda e: steps.append(e.detail) if e.kind == "step_started" else None)
    return ctx, steps


def run(ctx, web, api):
    return run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([]), renderer_factory=factory(FakeRenderer([1, 1])),
                        mailer=FakeMailer(), employer_fetch=web, employer_api=api)


def test_discovery_runs_between_search_and_fetch_and_recovered_alerts_are_evaluated_in_the_same_run(tmp_path):
    ctx, steps = make_ctx(tmp_path)
    alert = add_alert(ctx)
    api, calls = search_client(["https://www.cbc-cologne.de/karriere"])
    s = run(ctx, FakeWeb(cbc_pages()), api)
    assert s.errors == [] and len(calls) == 1  # one invocation, not two
    assert steps[:3] == ["search", "employer sites", "fetch"]
    assert s.employers.matched == 1 and s.employers.recovered == 1 and len(s.employers.related_created) == 1
    assert ctx.repo.get_job(alert)["status"] == "approved"  # evaluated in this run, after the recovery
    text = "\n".join(s.lines())
    assert "employer sites: 1 employers" in text and "alert vacancies: 1 matched" in text
    assert text.index("employer sites") < text.index("details:") < text.index("evaluate:")
    ctx.close()


def test_disabled_source_leaves_the_pipeline_unchanged(tmp_path):
    ctx, steps = make_ctx(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: false"))
    add_alert(ctx)
    api, calls = search_client([])
    s = run(ctx, FakeWeb(cbc_pages()), api)
    assert s.employers is None and "employer sites" not in steps and calls == []
    assert not any("employer" in line for line in s.lines())
    ctx.close()


def test_original_alerts_keep_priority_over_related_jobs_under_shared_limits(tmp_path):
    cfg = EMP_CONFIG.replace("max_evaluate_per_run: 20", "max_evaluate_per_run: 1")
    ctx, _ = make_ctx(tmp_path, config=cfg, evals=1)
    alert = add_alert(ctx)
    api, _ = search_client(["https://www.cbc-cologne.de/karriere"])
    s = run(ctx, FakeWeb(cbc_pages()), api)
    related = s.employers.related_created[0]
    assert s.evaluate.evaluated == 1 and s.evaluate.waiting == 1
    assert ctx.repo.get_job(alert)["status"] == "approved" and ctx.repo.get_job(related)["status"] == "new"
    ctx.close()


def test_search_failure_does_not_stop_the_rest_of_the_run(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    add_alert(ctx)
    api, _ = search_client([], status=503)
    s = run(ctx, FakeWeb(cbc_pages()), api)
    assert s.errors == [] and s.fetch is not None and s.evaluate is not None
    assert s.employers.employers[0].identity == "deferred" and s.employers.search_failures == 1
    ctx.close()


def test_fetch_reads_the_verified_employer_posting_not_the_platform_page(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    job = add_alert(ctx, status="new")
    ctx.conn.execute("INSERT INTO employers (id, display_name, status, created_at, updated_at) VALUES "
                     "('emp-1', 'CBC', 'verified', 'x', 'x')")
    ctx.conn.execute("INSERT INTO employer_seeds (job_id, employer_id, outcome, posting_url, checked_at) VALUES "
                     "(?, 'emp-1', 'matched', ?, 'x')", (job, TENANT + "details/346291.html"))
    ctx.conn.commit()
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text="""<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Data Engineer", "description": "<p>Beschreibung vom Arbeitgeber</p>",
 "hiringOrganization": {"name": "CBC"}}</script></head></html>""")

    ctx._http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(handler)), 0, 0, sleep=lambda s: None)
    summary = fetch_details(ctx)
    assert summary.fetched == 1 and seen == [TENANT + "details/346291.html"]
    assert ctx.repo.get_job(job)["job_url"].startswith("https://www.linkedin.com")  # the ad identity stays
    assert "vom Arbeitgeber" in ctx.repo.get_job(job)["description"]
    ctx.close()
