from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx

from autoapp.config.models import Location, SearchProfile
from autoapp.sources.adzuna import AdzunaSource

SEARCH = json.loads(
    (Path(__file__).resolve().parent.parent / "fixtures" / "adzuna" / "search.json").read_text(encoding="utf-8")
)
ENV = {"ADZUNA_APP_ID": "id1", "ADZUNA_APP_KEY": "key1"}


def make_source(requests: list[httpx.Request], env: dict[str, str] | None = ENV) -> AdzunaSource:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=SEARCH)

    return AdzunaSource(httpx.Client(transport=httpx.MockTransport(handler)), env=env, delay_s=0)


def test_ad_salary_range_captured_when_not_predicted() -> None:
    jobs = list(make_source([]).search(SearchProfile(name="p", keywords=["Data Scientist"])))
    job = jobs[0]
    assert (job.ad_salary_min, job.ad_salary_max) == (55000, 70000)
    assert job.salary_estimate_min is None and job.salary_estimate_max is None
    assert job.source == "adzuna" and job.source_job_id == "4901"
    assert job.company == "Acme Analytics GmbH"
    assert job.url == "https://www.adzuna.de/land/ad/4901?se=abc&v=123"
    assert job.posted_at == date(2026, 10, 5)
    assert job.working_time == "full_time" and job.contract_type == "permanent"


def test_predicted_salary_goes_to_estimate() -> None:
    jobs = list(make_source([]).search(SearchProfile(name="p", keywords=["x"])))
    job = jobs[1]
    assert job.ad_salary_min is None and job.ad_salary_max is None
    assert (job.salary_estimate_min, job.salary_estimate_max) == (48000, 52000)


def test_job_without_salary_and_contract_mapping() -> None:
    job = list(make_source([]).search(SearchProfile(name="p", keywords=["x"])))[2]
    assert job.ad_salary_min is None and job.salary_estimate_min is None
    assert job.contract_type == "fixed_term"


def test_profile_without_salary_filter_sends_no_salary_min() -> None:
    requests: list[httpx.Request] = []
    list(make_source(requests).search(SearchProfile(name="p", keywords=["Data Scientist"])))
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/api/jobs/de/search/1"
    assert dict(requests[0].url.params) == {
        "app_id": "id1",
        "app_key": "key1",
        "results_per_page": "50",
        "what": "Data Scientist",
    }


def test_fully_filtered_profile() -> None:
    requests: list[httpx.Request] = []
    profile = SearchProfile(
        name="p",
        keywords=["Python"],
        exclude_keywords=["Praktikum", "Werkstudent"],
        locations=[Location(name="Berlin", radius_km=25)],
        working_time="full_time",
        contract_type="permanent",
        max_posting_age_days=14,
        min_salary=60000,
    )
    list(make_source(requests).search(profile))
    params = dict(requests[0].url.params)
    assert params["what_exclude"] == "Praktikum Werkstudent"
    assert params["where"] == "Berlin" and params["distance"] == "25"
    assert params["max_days_old"] == "14" and params["salary_min"] == "60000"
    assert params["full_time"] == "1" and params["permanent"] == "1"
    assert "part_time" not in params and "contract" not in params


def test_part_time_and_contract_flags() -> None:
    requests: list[httpx.Request] = []
    profile = SearchProfile(name="p", keywords=["x"], working_time="part_time", contract_type="fixed_term")
    list(make_source(requests).search(profile))
    params = dict(requests[0].url.params)
    assert params["part_time"] == "1" and params["contract"] == "1"
    assert "full_time" not in params and "permanent" not in params


def test_unconfigured_is_skipped_without_requests() -> None:
    requests: list[httpx.Request] = []
    src = make_source(requests, env={})
    assert src.skip_reason() == "adzuna: skipped (not configured)"
    assert list(src.search(SearchProfile(name="p", keywords=["x"]))) == []
    assert requests == []


def test_configured_has_no_skip_reason() -> None:
    assert make_source([]).skip_reason() is None


def test_http_error_propagates() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    src = AdzunaSource(client, env=ENV, delay_s=0)
    try:
        list(src.search(SearchProfile(name="p", keywords=["x"])))
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 503
    else:  # pragma: no cover
        raise AssertionError("expected HTTPStatusError")


# --- request budget and rate limits ------------------------------------------------------------


def _budgeted(requests, *, per_run=40, per_day=150, per_month=900, status=200, conn=None, today=date(2026, 10, 8)):
    import sqlite3

    from autoapp.db.schema import migrate
    from autoapp.sources.budget import RequestBudget

    if conn is None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=SEARCH if status == 200 else {})

    budget = RequestBudget(conn, "adzuna", per_run=per_run, per_day=per_day, per_month=per_month, today=today)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AdzunaSource(client, env=ENV, delay_s=0, budget=budget), conn


def test_default_pacing_stays_under_25_requests_a_minute() -> None:
    from autoapp.sources.adzuna import MIN_INTERVAL_S

    assert 60 / MIN_INTERVAL_S < 25
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SEARCH)))
    src = AdzunaSource(client, env=ENV, sleep=sleeps.append)
    list(src.search(SearchProfile(name="p", keywords=["a", "b", "c"])))
    assert sleeps == [MIN_INTERVAL_S, MIN_INTERVAL_S]  # nothing before the first request


def test_per_run_cap_stops_the_search_and_keeps_found_jobs() -> None:
    requests: list[httpx.Request] = []
    src, _ = _budgeted(requests, per_run=2)
    jobs = list(src.search(SearchProfile(name="p", keywords=["a", "b", "c", "d"])))
    assert len(requests) == 2 and jobs
    assert "per-run request limit reached (2)" in (src.stop_reason or "")


def test_daily_cap_holds_across_runs() -> None:
    requests: list[httpx.Request] = []
    first, conn = _budgeted(requests, per_day=3)
    list(first.search(SearchProfile(name="p", keywords=["a", "b"])))
    second, _ = _budgeted(requests, per_day=3, conn=conn)  # a later run on the same day
    list(second.search(SearchProfile(name="p", keywords=["c", "d"])))
    assert len(requests) == 3
    assert "daily request limit reached (3)" in (second.stop_reason or "")


def test_monthly_cap_counts_earlier_days_and_resets_next_month() -> None:
    requests: list[httpx.Request] = []
    old, conn = _budgeted(requests, today=date(2026, 10, 1))
    list(old.search(SearchProfile(name="p", keywords=["a", "b", "c"])))
    assert len(requests) == 3
    same_month, _ = _budgeted(requests, per_month=3, conn=conn, today=date(2026, 10, 20))
    assert list(same_month.search(SearchProfile(name="p", keywords=["x"]))) == []
    assert "monthly request limit reached (3)" in (same_month.stop_reason or "")
    next_month, _ = _budgeted(requests, per_month=3, conn=conn, today=date(2026, 11, 2))
    assert list(next_month.search(SearchProfile(name="p", keywords=["x"])))
    assert next_month.stop_reason is None


def test_429_stops_cleanly_instead_of_raising() -> None:
    requests: list[httpx.Request] = []
    src, _ = _budgeted(requests, status=429)
    assert list(src.search(SearchProfile(name="p", keywords=["a", "b", "c"]))) == []
    assert len(requests) == 1  # no hammering after the first 429
    assert "HTTP 429" in (src.stop_reason or "")


def test_no_request_is_made_once_stopped() -> None:
    requests: list[httpx.Request] = []
    src, _ = _budgeted(requests, per_run=1)
    list(src.search(SearchProfile(name="p", keywords=["a"], locations=[Location(name="Berlin")])))
    list(src.search(SearchProfile(name="p2", keywords=["b"])))
    assert len(requests) == 1
