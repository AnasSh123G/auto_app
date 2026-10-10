from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from autoapp.config.models import Location, SearchProfile
from autoapp.db.schema import open_db
from autoapp.sources.arbeitnow import BASE_URL, ArbeitnowSource, listing_url, source_link
from autoapp.sources.budget import RequestBudget

FEED = json.loads((Path(__file__).parents[1] / "fixtures/arbeitnow/feed.json").read_text())
TODAY = date(2026, 10, 9)


def profile(name="backend", **kw):
    return SearchProfile(name=name, keywords=["Backend Developer"], **kw)


def make_source(pages=None, *, profiles=None, budget=None, max_pages=3):
    requests, sleeps = [], []
    responses = pages if pages is not None else [FEED]

    def handler(request):
        requests.append(request)
        value = responses[len(requests) - 1]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ArbeitnowSource(client, profiles=profiles, budget=budget, max_pages=max_pages,
                          delay_s=3, sleep=sleeps.append, today=TODAY), requests, sleeps


def feed_item(**updates):
    item = copy.deepcopy(FEED["data"][0])
    item.update(updates)
    return item


def page(items, next_page=None):
    return {"data": items, "links": {"next": next_page}}


def test_current_schema_html_facts_and_source_timestamp():
    src, req, _ = make_source()
    jobs = list(src.search(profile()))
    assert len(jobs) == 1 and len(req) == 1
    j = jobs[0]
    assert j.source == "arbeitnow" and j.source_job_id == FEED["data"][0]["slug"]
    assert j.company == "Muster GmbH" and j.location == "Berlin"
    assert "Build Python" in j.description and "- PostgreSQL" in j.description
    assert "<" not in j.description and "not part of the job" not in j.description
    assert j.work_mode is None and j.working_time == "full_time" and j.contract_type == "permanent"
    assert j.application_url is None and j.ad_salary_min is None and j.salary_estimate_min is None
    assert j.posted_at == datetime.fromtimestamp(FEED["data"][0]["created_at"], UTC).date()
    assert "not employer publication" in j.raw["date_basis"]
    assert dict(req[0].url.params) == {"page": "1"}
    assert req[0].headers["user-agent"].startswith("autoapp/")
    assert src.diagnostics["foreign"] == 1


@pytest.mark.parametrize("field,value", [
    ("created_at", "yesterday"), ("created_at", -1), ("created_at", 10**30),
    ("created_at", 9999999999), ("created_at", True), ("remote", "true"),
    ("job_types", ["Permanent", 12]), ("tags", 3), ("description", 8), ("location", False),
])
def test_invalid_optional_facts_are_unknown_and_reported(field, value):
    src, _, _ = make_source([page([feed_item(**{field: value})])])
    jobs = list(src.search(profile()))
    assert len(jobs) == 1 and "invalid optional " + field in src.report_note
    if field == "created_at":
        assert jobs[0].posted_at is None
    if field == "remote":
        assert jobs[0].work_mode is None


def test_missing_optional_facts_and_ambiguous_types():
    item = feed_item()
    for k in ("tags", "job_types", "remote", "description", "created_at", "location"):
        item.pop(k)
    other = feed_item(slug="other", job_types=["Full Time", "Part Time", "Mystery"])
    src, _, _ = make_source([page([item, other])])
    jobs = list(src.search(profile()))
    assert len(jobs) == 2 and jobs[0].posted_at is None and jobs[0].description is None
    assert jobs[1].working_time is None and "Mystery" in jobs[1].raw["job_types"]


@pytest.mark.parametrize("update", [{"slug": ""}, {"company_name": None}, {"title": " "},
                                   {"url": "javascript:alert(1)"}])
def test_invalid_record_keeps_valid_siblings(update):
    src, _, _ = make_source([page([feed_item(**update), feed_item(slug="valid")])])
    assert [j.source_job_id for j in src.search(profile())] == ["valid"]
    assert src.diagnostics["invalid"] == 1 and src.report_note


def test_multiple_profiles_share_feed_and_accepting_profile_wins():
    a = profile("exclude", exclude_position_types=["internship"])
    b = profile("accept")
    src, req, _ = make_source([page([feed_item(job_types=["Internship"])])], profiles=[a, b])
    assert list(src.search(a)) == []
    jobs = list(src.search(b))
    assert len(jobs) == 1 and jobs[0].search_profile == "accept"
    assert jobs[0].raw["profiles"] == ["exclude", "accept"]
    assert jobs[0].raw["accepting_profiles"] == ["accept"]
    assert len(req) == 1


def test_all_rejected_profile_is_still_yielded_for_normal_filtering():
    a = profile(exclude_keywords=["Backend"])
    src, _, _ = make_source(profiles=[a])
    jobs = list(src.search(a))
    assert len(jobs) == 1 and jobs[0].raw["accepting_profiles"] == []


@pytest.mark.parametrize("field,value", [
    ("title", "Backend Developer"), ("tags", ["Backend Developer"]),
    ("description", "<p>Looking for a Backend Developer.</p>"),
])
def test_keyword_matching_in_each_supported_field(field, value):
    item = feed_item(title="Other role", tags=[], description="")
    item[field] = value
    src, _, _ = make_source([page([item])])
    assert len(list(src.search(profile()))) == 1


def test_keyword_boundaries():
    src, _, _ = make_source([page([feed_item(title="Backend Developers", tags=[], description="")])])
    assert list(src.search(profile())) == []


@pytest.mark.parametrize("country", ["France", "FR", "Austria", "CH", "United States", "Australia"])
def test_explicit_foreign_country_excluded_even_on_german_market_url(country):
    src, _, _ = make_source([page([feed_item(country=country)])])
    assert list(src.search(profile())) == [] and src.diagnostics["foreign"] == 1


@pytest.mark.parametrize("location,expected,uncertain", [
    ("Berlin", True, False), ("Munich", False, False), ("Cologne", False, False),
    ("Unknown village", True, True), ("Remote", True, True), ("Paris, France", False, False),
])
def test_local_geography(location, expected, uncertain):
    p = profile(locations=[Location(name="Berlin", radius_km=30)])
    src, _, _ = make_source([page([feed_item(location=location, remote=True)])], profiles=[p])
    jobs = list(src.search(p))
    assert bool(jobs) is expected
    if jobs:
        assert jobs[0].raw["location_uncertain"] is uncertain


def test_exact_city_and_radius_boundaries():
    for radius, expected in ((None, False), (25, False), (30, True)):
        p = profile(locations=[Location(name="Frankfurt", radius_km=radius)])
        src, _, _ = make_source([page([feed_item(location="Darmstadt")])], profiles=[p])
        assert bool(list(src.search(p))) is expected
    p = profile(locations=[Location(name="Cologne")])
    src, _, _ = make_source([page([feed_item(location="Köln")])], profiles=[p])
    assert len(list(src.search(p))) == 1


def test_pages_shared_pacing_dedup_and_null_next():
    p, q = profile(), SearchProfile(name="python", keywords=["Python"])
    first = page([feed_item()], BASE_URL + "?page=2")
    second = page([feed_item(), feed_item(slug="second", url=FEED["data"][0]["url"] + "-second")])
    src, req, sleeps = make_source([first, second], profiles=[p, q])
    assert len(list(src.search(p))) == 2
    assert list(src.search(q)) == []
    assert len(req) == 2 and sleeps == [3]


@pytest.mark.parametrize("target", [
    "http://www.arbeitnow.com/api/job-board-api?page=2",
    "https://evil.com/api/job-board-api?page=2",
    "https://127.0.0.1/api/job-board-api?page=2",
    "https://www.arbeitnow.com/private?page=2",
    BASE_URL + "?page=1", BASE_URL + "?page=3", BASE_URL + "?page=2&token=secret",
    BASE_URL + "?page=2&page=2", BASE_URL + "?page=2#fragment",
    "https://u:p@www.arbeitnow.com/api/job-board-api?page=2",
    "https://www.arbeitnow.com:8443/api/job-board-api?page=2", 2,
])
def test_unsafe_or_looping_pagination_never_requested(target):
    src, req, _ = make_source([page([feed_item()], target)])
    assert len(list(src.search(profile()))) == 1
    assert len(req) == 1 and "pagination" in src.stop_reason


@pytest.mark.parametrize("bad", [
    {"data": []}, {"data": "not a list", "links": {"next": None}},
    httpx.Response(200, text="not json"), httpx.Response(302, headers={"Location": "http://localhost/"}),
    httpx.Response(403), httpx.Response(429), httpx.Response(999), httpx.Response(500),
    httpx.ConnectError("offline"),
])
def test_later_page_failure_keeps_earlier_jobs_no_retry(bad):
    p, q = profile(), profile("other")
    src, req, _ = make_source([page([feed_item()], BASE_URL + "?page=2"), bad], profiles=[p, q])
    assert len(list(src.search(p))) == 1
    assert list(src.search(q)) == []
    assert len(req) == 2 and "error" in src.stop_reason


def test_page_cap_is_partial():
    src, req, _ = make_source([page([feed_item()], BASE_URL + "?page=2")], max_pages=1)
    assert len(list(src.search(profile()))) == 1
    assert len(req) == 1 and "page limit" in src.stop_reason


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "https://www.arbeitnow.com.evil.test/jobs/x",
    "https://evil@www.arbeitnow.com/jobs/x", "https://www.arbeitnow.fr/jobs/x",
    "https://www.arbeitnow.com:8443/jobs/x", "https://www.arbeitnow.com/jobs/../private",
    "https://www.arbeitnow.com/jobs/%0Ainjection", "https://www.arbeitnow.com/private",
])
def test_source_links_reject_unsafe_targets(url):
    assert listing_url(url) is None
    assert source_link([{"source": "arbeitnow", "url": url}]) is None


def test_source_link_canonicalizes_and_preserves_secondary_attribution():
    url = "http://arbeitnow.com/jobs/test?utm_source=secret#fragment"
    assert source_link([{"source": "adzuna", "url": "https://adzuna.de/x"},
                        {"source": "arbeitnow", "url": url}]) == "https://www.arbeitnow.com/jobs/test"


@pytest.mark.parametrize("cap", ["run", "day", "month"])
def test_exact_budget_boundary_and_reopen(tmp_path, cap):
    path = tmp_path / "db.sqlite"
    conn = open_db(path)
    limits = {"per_run": 3, "per_day": 5, "per_month": 7}
    if cap == "run":
        limits["per_run"] = 1
    else:
        count = limits["per_" + cap]
        stored = TODAY.isoformat() if cap == "day" else "2026-10-01"
        conn.execute("INSERT INTO api_usage VALUES ('arbeitnow', ?, ?)", (stored, count))
        conn.commit()
    budget = RequestBudget(conn, "arbeitnow", today=TODAY, **limits)
    src, req, _ = make_source([page([feed_item()], BASE_URL + "?page=2")], budget=budget)
    assert len(list(src.search(profile()))) == (1 if cap == "run" else 0)
    assert len(req) == (1 if cap == "run" else 0)
    assert "limit" in src.stop_reason
    conn.close()
    conn = open_db(path)
    total = conn.execute("SELECT SUM(requests) FROM api_usage WHERE source='arbeitnow'").fetchone()[0]
    assert total == (1 if cap == "run" else count)
    conn.close()


def test_failed_attempt_is_counted_and_client_ownership(tmp_path):
    conn = open_db(tmp_path / "db.sqlite")
    budget = RequestBudget(conn, "arbeitnow", per_run=3, per_day=3, per_month=3, today=TODAY)
    src, req, _ = make_source([httpx.ReadTimeout("timeout")], budget=budget)
    assert list(src.search(profile())) == []
    assert budget.used_this_run == 1 and len(req) == 1
    assert not src._client.is_closed
    src._client.close()
    conn.close()


def test_owned_client_closed_after_failure(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    monkeypatch.setattr("autoapp.sources.arbeitnow.httpx.Client", lambda **kw: client)
    assert list(ArbeitnowSource(today=TODAY).search(profile())) == []
    assert client.is_closed
