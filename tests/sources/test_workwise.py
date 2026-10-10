"""Workwise search (recorded answers from the real API) and the provisional Workwise alert parser."""

from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest

from autoapp.config.models import Location, SearchProfile
from autoapp.fetch.http_fetcher import USER_AGENT, HttpFetcher
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.alerts.registry import parse_email
from autoapp.sources.workwise import WorkwiseSource
from autoapp.steps.context import RunContext
from autoapp.steps.fetch import fetch_details
from autoapp.steps.search import run_search
from tests.helpers import make_workspace

FIX = Path(__file__).parent.parent / "fixtures" / "workwise"
SEARCH = json.loads((FIX / "search.json").read_text(encoding="utf-8"))
JOB_PAGE = (FIX / "job_129201.html").read_text(encoding="utf-8")


def _client(requests: list, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=SEARCH) if status == 200 else httpx.Response(status)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _source(requests, **kw):
    return WorkwiseSource(_client(requests, kw.pop("status", 200)), sleep=lambda s: None, **kw)


def test_jobs_are_read_from_the_search_answer():
    requests = []
    jobs = list(_source(requests).search(SearchProfile(name="p", keywords=["Pflege"])))
    assert [j.source_job_id for j in jobs] == ["129201", "129200", "129187"]
    first = jobs[0]
    assert first.title == "Stellvertretende Wohnbereichsleitung in der Pflege (m/w/d)"
    assert first.company == "Oberlausitz Pflegeheim & Kurzzeitpflege gGmbH" and first.location == "Bischofswerda"
    assert first.url == "https://www.workwise.io/job/129201-stellvertretende-wohnbereichsleitung-in-der-pflege-m-w-d"
    assert (first.ad_salary_min, first.ad_salary_max) == (51480, 57804)
    assert (first.work_mode, first.working_time, first.contract_type) == ("onsite", "full_time", "permanent")
    assert first.description is None  # read from the job page by the fetch step
    assert jobs[1].working_time == "part_time" and jobs[2].ad_salary_min is None
    request = requests[0]
    assert request.method == "POST" and request.url.host == "search.workwise.io"
    assert json.loads(request.content)["description"] == "Pflege"
    assert request.headers["user-agent"] == USER_AGENT and request.url.params["size"] == "50"


def test_only_jobs_near_the_profiles_cities_are_kept():
    requests = []
    source = _source(requests)
    near_dresden = SearchProfile(name="p", keywords=["Pflege"], locations=[Location(name="Dresden", radius_km=50)])
    jobs = list(source.search(near_dresden))
    assert [j.location for j in jobs] == ["Bischofswerda", "Bischofswerda"]  # Witten is far away
    assert source.diagnostics["outside your area"] == 1


def test_a_job_found_by_two_keywords_is_kept_once():
    requests = []
    jobs = list(_source(requests).search(SearchProfile(name="p", keywords=["Pflege", "Recruiter"])))
    assert len(requests) == 2 and len(jobs) == 3


def test_limits_and_errors_stop_the_search_without_retrying():
    class NoBudget:
        reason = "daily request limit reached (100); the rest follows on the next run"

        def take(self):
            return False

    requests = []
    source = _source(requests, budget=NoBudget())
    assert list(source.search(SearchProfile(name="p", keywords=["Pflege"]))) == [] and requests == []
    assert source.stop_reason.startswith("partial: daily request limit")
    failing = _source(requests, status=500)
    assert list(failing.search(SearchProfile(name="p", keywords=["Pflege", "Recruiter"]))) == []
    assert len(requests) == 1 and failing.stop_reason == "partial/error: HTTP 500; stopped without retry"


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def _page_fetcher():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.workwise.io" and request.url.path.startswith("/job/129201"):
            return httpx.Response(200, text=JOB_PAGE, headers={"Content-Type": "text/html"})
        return httpx.Response(404)
    return HttpFetcher(httpx.Client(transport=httpx.MockTransport(handler)), 0, 0, sleep=lambda s: None)


def test_workwise_jobs_go_through_the_normal_pipeline(ctx):
    for profile in ctx.config.search_profiles:
        profile.locations = None
    requests = []
    summary = run_search(ctx, api_sources=[_source(requests)], mailbox=FakeMailbox([]))
    assert summary.sources["workwise"].new == 3
    fetch_details(ctx, http=_page_fetcher())
    job = next(j for j in ctx.repo.jobs_by_status("new") if "Wohnbereichsleitung" in j["title"])
    assert "Pflegefachkraft" in job["description"]  # the full description from the job page
    assert job["application_url"] == "https://www.workwise.io/job/129201"


def _alert(html: str, text: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Workwise <jobs@mail.workwise.io>"
    msg["Subject"] = "Neue Jobs für dich"
    msg.set_content(text or "Neue Jobs")
    msg.add_alternative(html, subtype="html")
    return msg


ALERT_HTML = """<html><body>
<a href="https://click.mail.workwise.io/ls/click?upn=abc%2Fjob%2F129201-stellvertretende-wohnbereichsleitung-in-der-pflege-m-w-d%3Futm%3Dx">Job ansehen</a>
<a href="https://www.workwise.io/job/129201-stellvertretende-wohnbereichsleitung-in-der-pflege-m-w-d">Stellvertretende Wohnbereichsleitung in der Pflege (m/w/d)</a>
<a href="https://www.workwise.io/job/129187-recruiter-schwerpunkt-active-sourcing">Mehr erfahren</a>
<a href="https://www.workwise.io/einstellungen">Einstellungen</a>
</body></html>"""


def test_the_alert_parser_finds_every_job_link_and_never_trusts_button_texts():
    result = parse_email(_alert(ALERT_HTML, "Auch hier: https://www.workwise.io/job/128087-softwareentwickler-m-w-d"))
    assert result.parser_name == "workwise" and not result.warnings
    cards = {c.source_job_id: c for c in result.cards}
    assert set(cards) == {"129201", "129187", "128087"}
    assert cards["129201"].title == "Stellvertretende Wohnbereichsleitung in der Pflege (m/w/d)"
    assert not cards["129201"].title_truncated
    assert cards["129187"].title == "Recruiter schwerpunkt active sourcing" and cards["129187"].title_truncated
    assert all(c.company_unknown and c.source == "workwise-alert" for c in cards.values())


def test_alert_jobs_get_their_company_and_description_from_the_job_page(ctx):
    run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<w1@workwise>", _alert(ALERT_HTML))]))
    fetch_details(ctx, http=_page_fetcher())
    job = next(j for j in ctx.repo.jobs_by_status("new") if j["description"])
    assert job["company"] == "Oberlausitz Pflegeheim & Kurzzeitpflege gGmbH"
    assert "Pflegefachkraft" in job["description"]


def test_an_email_without_job_links_says_the_parser_may_be_outdated():
    result = parse_email(_alert("<html><body><a href='https://www.workwise.io/'>Workwise</a></body></html>"))
    assert result.cards == [] and "parser may be outdated" in result.warnings[0]
