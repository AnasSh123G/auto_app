"""The detail step in search-first alert mode reads no job-board page for alert jobs (alert-resolution spec)."""

from __future__ import annotations

import httpx
import pytest

from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.employers.repo import EmployerRepo
from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.sources.alerts.models import AlertCard
from autoapp.steps.context import RunContext
from autoapp.steps.fetch import fetch_details
from tests.helpers import make_workspace
from tests.steps.test_other_steps import PAGE

URLS = {"linkedin-alert": "https://www.linkedin.com/jobs/view/{n}/", "stepstone-alert": "https://www.stepstone.de/stellenangebote--{n}-inline.html",
        "indeed-alert": "https://de.indeed.com/viewjob?jk={n}", "workwise-alert": "https://www.workwise.io/job/{n}-x"}


class Browser:
    on_wait, blocked_sites = None, {}

    def __init__(self):
        self.urls = []

    def fetch(self, url):
        from autoapp.fetch.http_fetcher import FetchResult

        self.urls.append(url)
        return FetchResult(url=url, status=200, html=PAGE, final_url=url)


@pytest.fixture(params=["search_first", "platform"])
def mode(request):
    return request.param


def make_ctx(tmp_path, mode):
    return RunContext.open(make_workspace(tmp_path, alert_mode=mode))


def http_seen(seen):
    client = httpx.Client(transport=httpx.MockTransport(lambda req: seen.append(str(req.url)) or httpx.Response(200, text=PAGE)))
    return HttpFetcher(client, 0, 0, sleep=lambda s: None)


def alert(ctx, source, n, **kw):
    card = AlertCard(source=source, title=f"Backend Developer {n} (m/w/d)", company=kw.pop("company", "A GmbH"),
                     locations=["Berlin"], source_job_id=str(n), url=URLS[source].format(n=n), **kw)
    return ingest(ctx.repo, card_to_raw(card)).job_id


def test_no_job_board_page_is_requested_for_any_alert_source(tmp_path):
    ctx = make_ctx(tmp_path, "search_first")
    ids = [alert(ctx, source, n) for n, source in enumerate(URLS, start=1)]
    browser, seen = Browser(), []
    summary = fetch_details(ctx, http=http_seen(seen), browser=browser)
    assert browser.urls == [] and seen == [] and summary.fetched == 0 and summary.unavailable == 4
    for job_id in ids:
        job = ctx.repo.get_job(job_id)
        assert job["status"] == "description_unavailable"
    ctx.close()


def test_the_reason_of_the_employer_search_is_the_note(tmp_path):
    ctx = make_ctx(tmp_path, "search_first")
    missed = alert(ctx, "linkedin-alert", 1)
    nameless = alert(ctx, "workwise-alert", 2, company="", company_unknown=True)
    plain = alert(ctx, "stepstone-alert", 3)
    ctx.alert_notes[missed] = "employer ad not found: kununu.com: not readable"
    fetch_details(ctx, http=http_seen([]), browser=Browser())
    notes = {row["job_id"]: row["note"] for row in ctx.conn.execute(
        "SELECT job_id, note FROM status_events WHERE to_status = 'description_unavailable'")}
    assert "employer ad not found: kununu.com" in notes[missed]
    assert notes[nameless] == "alert does not name the employer"
    assert notes[plain] == "employer ad not found"
    ctx.close()


def test_a_matched_employer_posting_is_read_instead_of_the_board_page(tmp_path):
    ctx = make_ctx(tmp_path, "search_first")
    job_id = alert(ctx, "linkedin-alert", 1)
    EmployerRepo(ctx.conn).record_seed(job_id, "matched", posting_url="https://jobs.a-gmbh.de/1", reason="test")
    browser, seen = Browser(), []
    summary = fetch_details(ctx, http=http_seen(seen), browser=browser)
    assert summary.fetched == 1 and seen == ["https://jobs.a-gmbh.de/1"] and browser.urls == []
    assert ctx.repo.get_job(job_id)["description"]
    ctx.close()


def test_other_jobs_are_fetched_as_before_and_platform_mode_reads_the_board(tmp_path, mode):
    ctx = make_ctx(tmp_path, mode)
    manual = ctx.repo.insert_job(title="Backend Developer", company="A GmbH", job_url="https://jobs.a-gmbh.de/9")
    linkedin = alert(ctx, "linkedin-alert", 1)
    browser, seen = Browser(), []
    summary = fetch_details(ctx, http=http_seen(seen), browser=browser)
    assert "https://jobs.a-gmbh.de/9" in seen and ctx.repo.get_job(manual)["description"]
    if mode == "platform":
        assert browser.urls == [URLS["linkedin-alert"].format(n=1)] and summary.fetched == 2
        assert ctx.repo.get_job(linkedin)["description"]
    else:
        assert browser.urls == [] and ctx.repo.get_job(linkedin)["status"] == "description_unavailable"
    ctx.close()


def test_no_request_goes_to_a_job_board_from_the_search_to_the_detail_step(tmp_path):
    """Search, careers pages and detail step together: every host asked for is the employer's, never a board's."""
    from urllib.parse import urlsplit

    from autoapp.employers.service import find_alert_ads
    from tests.employers.identity_world import search_client
    from tests.employers.test_service import EMP_CONFIG
    from tests.employers.webfake import FakeWeb

    ctx = RunContext.open(make_workspace(tmp_path, config=EMP_CONFIG, alert_mode="search_first"))
    ids = [alert(ctx, source, n) for n, source in enumerate(URLS, start=1)]
    boards = [v.format(n=1) for v in URLS.values()]
    api, calls = search_client({"Find the job ad": boards})  # the search only ever returns job-board pages
    web = FakeWeb({})
    find_alert_ads(ctx, fetch=web, api=api)
    browser, seen = Browser(), []
    fetch_details(ctx, http=http_seen(seen), browser=browser)
    requested = {urlsplit(u).hostname for u in (*web.calls, *seen, *browser.urls)}
    board_hosts = {"www.linkedin.com", "www.stepstone.de", "de.indeed.com", "www.workwise.io"}
    assert calls and not requested & board_hosts and browser.urls == [] and seen == []
    assert all(ctx.repo.get_job(j)["status"] == "description_unavailable" for j in ids)
    ctx.close()
