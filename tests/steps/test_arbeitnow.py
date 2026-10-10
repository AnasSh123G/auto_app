from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import httpx

from autoapp.config.models import SearchProfile
from autoapp.dashboard.queries import compute_stats
from autoapp.dashboard.wiring import attach_run_events
from autoapp.db.status import Status
from autoapp.discovery.ingest import ingest
from autoapp.llm.cost import PriceTable
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.steps.context import RunContext
from autoapp.steps.fetch import fetch_details
from autoapp.steps.pipeline import run_pipeline
from autoapp.steps.search import _default_sources, run_search
from tests.helpers import make_workspace
from tests.sources.test_arbeitnow import FEED, TODAY, feed_item, make_source, page
from tests.steps.test_other_steps import FakeSource
from tests.steps.test_pipeline import EVAL
from tests.steps.test_prepare_step import AD, LETTER, FakeRenderer, factory


def test_factory_disabled_and_profile_selector(tmp_path, monkeypatch):
    ctx = RunContext.open(make_workspace(tmp_path))
    ctx.config.sources.arbeitsagentur.enabled = ctx.config.sources.adzuna.enabled = False
    ctx.config.sources.workwise.enabled = False
    assert _default_sources(ctx) == []
    ctx.config.sources.arbeitnow.enabled = True
    ctx.config.search_profiles.append(SearchProfile(name="accounting", keywords=["Accountant"]))
    req = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: (req.append(r), httpx.Response(200, json=FEED))[1]))
    monkeypatch.setattr("autoapp.sources.arbeitnow.httpx.Client", lambda **kw: client)
    s = run_search(ctx, "accounting", mailbox=FakeMailbox([]), today=TODAY)
    assert len(req) == 1 and s.sources["arbeitnow"].found == s.sources["arbeitnow"].new == 1
    job = ctx.repo.jobs_by_status("new")[0]
    assert job["title"] == "Accountant" and job["search_profile"] == "accounting"
    assert client.is_closed
    assert ctx.conn.execute("SELECT requests FROM api_usage WHERE source='arbeitnow'").fetchone()[0] == 1
    ctx.close()


def test_pipeline_recovers_alert_adds_related_profile_and_attributes_without_sending(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path), dry_run=True)
    attach_run_events(ctx)
    ctx.mode = "automatic"
    first = ctx.config.search_profiles[0]
    first.exclude_position_types = ["internship"]
    second = SearchProfile(name="python", keywords=["Python"])
    ctx.config.search_profiles.append(second)
    alert = ingest(ctx.repo, RawJob(
        source="linkedin-alert", source_job_id="1", title="Backend Developer (m/w/d)",
        company="Muster GmbH", location="Berlin", url="https://www.linkedin.com/jobs/view/12345678/",
        search_profile=first.name,
    )).job_id
    original = feed_item(description="<p>" + AD + "</p>")
    added = feed_item(slug="second-role", company_name="Second GmbH", job_types=["Internship"],
                      description="<p>" + AD + "</p>", url="https://www.arbeitnow.com/jobs/companies/second/role")
    src, req, _ = make_source([page([original, added, FEED["data"][1]])], profiles=[first, second])
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [EVAL, EVAL]})
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER, LETTER]})
    mailer = FakeMailer()
    s = run_pipeline(ctx, api_sources=[src], mailbox=FakeMailbox([]),
                     renderer_factory=factory(FakeRenderer([1])), mailer=mailer)
    assert s.ok, s.errors
    assert len(req) == 1 and s.search.sources["arbeitnow"].found == 2
    assert s.search.sources["arbeitnow"].new == 1 and s.search.sources["arbeitnow"].linked == 1
    assert s.evaluate.approved == s.prepare.prepared == 2 and mailer.sent == []
    jobs = ctx.repo.jobs_by_status(Status.PREPARED)
    assert len(jobs) == 2
    new_job = next(j for j in jobs if j["id"] != alert)
    assert new_job["search_profile"] == "python"
    assert ctx.repo.get_job(alert)["job_url"].startswith("https://www.linkedin.com/")
    assert s.fetch.fetched == 0  # Both descriptions came from the public feed, no browser/detail request.
    assert s.employers is None
    assert [r[0] for r in ctx.conn.execute(
        "SELECT job_id FROM run_events WHERE kind='job_approved' ORDER BY rowid")] == [alert, new_job["id"]]
    for job in jobs:
        folder = Path(ctx.repo.get_application(job["id"])["folder"])
        for name in ("job.md", "README.md", "apply.html"):
            text = (folder / name).read_text()
            assert "Arbeitnow" in text and "https://www.arbeitnow.com/jobs/" in text
        for name in ("cover_letter.md", "email.eml"):
            assert "Arbeitnow" not in (folder / name).read_text()
    rows = list(csv.DictReader(ctx.ws.csv_path.open(encoding="utf-8-sig"), delimiter=";"))
    assert len(rows) == 2 and all(r["arbeitnow_url"].startswith("https://www.arbeitnow.com/jobs/") for r in rows)
    assert "foreign: 1" in "\n".join(s.lines())
    stats = compute_stats(ctx.conn, ctx.config, PriceTable.load({}), "run")
    assert stats.sources["arbeitnow"].details["foreign"] == 1
    assert stats.sources["arbeitnow"].found == 2
    # A later duplicate updates provenance, not status, notes, evaluation or edited package content.
    old = ctx.repo.get_application(alert)
    edited = Path(old["folder"]) / "job.md"
    edited.write_text("My edited job snapshot")
    ctx.repo.update_job(alert, notes="my note")
    before = ctx.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    repeat, repeat_requests, _ = make_source([page([original])], profiles=[first, second])
    again = run_search(ctx, api_sources=[repeat], mailbox=FakeMailbox([]), today=TODAY)
    assert len(repeat_requests) == 1 and again.sources["arbeitnow"].linked == 1
    assert edited.read_text() == "My edited job snapshot"
    assert ctx.repo.get_job(alert)["status"] == "prepared" and ctx.repo.get_job(alert)["notes"] == "my note"
    assert ctx.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == before
    ctx.close()


def test_all_profiles_reject_counts_filtered_and_failure_isolated(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path))
    ctx.config.search_profiles[0].exclude_position_types = ["internship"]
    src, _, _ = make_source([page([feed_item(job_types=["Internship"])])],
                            profiles=ctx.config.search_profiles)
    s = run_search(ctx, api_sources=[src], mailbox=FakeMailbox([]), today=TODAY)
    assert s.sources["arbeitnow"].filtered == 1 and ctx.repo.jobs_by_status("filtered")
    failed, req, _ = make_source([httpx.Response(429)], profiles=ctx.config.search_profiles)
    other = FakeSource([RawJob(source="fakeapi", source_job_id="1", title="Other", company="Other")])
    s = run_search(ctx, api_sources=[failed, other], mailbox=FakeMailbox([]))
    assert len(req) == 1 and "429" in s.sources["arbeitnow"].note
    assert s.sources["fakeapi"].new == 1
    ctx.close()


def test_missing_description_uses_existing_unavailable_path(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path))
    src, _, _ = make_source([page([feed_item(description=None)])], profiles=ctx.config.search_profiles)
    run_search(ctx, api_sources=[src], mailbox=FakeMailbox([]), today=TODAY)
    from autoapp.fetch.http_fetcher import HttpFetcher

    req = []
    http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(
        lambda r: (req.append(r), httpx.Response(404))[1])))
    s = fetch_details(ctx, http=http)
    assert len(req) == 1 and s.unavailable == 1
    assert ctx.repo.jobs_by_status("description_unavailable")
    http.close()
    ctx.close()


def test_request_statistics_visible_even_after_source_disabled(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path))
    day = datetime.now(UTC).date().isoformat()
    ctx.conn.execute("INSERT INTO api_usage VALUES ('arbeitnow', ?, 2)", (day,))
    ctx.conn.commit()
    stats = compute_stats(ctx.conn, ctx.config, PriceTable.load({}), "all")
    assert stats.arbeitnow == {"today": (2, 200), "this month": (2, 1000)}
    assert not ctx.config.sources.arbeitnow.enabled
    ctx.close()
