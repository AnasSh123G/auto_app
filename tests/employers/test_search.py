"""Parallel Fast adapter, budget and failure handling (tasks 2.1 to 2.4)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from autoapp.config.models import EmployerCareersConfig
from autoapp.db.schema import open_db
from autoapp.employers.gateway import SearchGateway, SearchUnavailableForRun
from autoapp.employers.hints import build_search, sanitize_hint
from autoapp.employers.parallel import (
    ParallelSearch,
    SearchAuthError,
    SearchMalformed,
    SearchNoKey,
    SearchRateLimited,
    SearchRedirected,
    SearchUnavailable,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "parallel_search_v1.json").read_text())


class Recorder:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def client(handler, key="pk-secret-1234567890") -> tuple[ParallelSearch, Recorder]:
    rec = Recorder(handler)
    http = httpx.Client(transport=httpx.MockTransport(rec), follow_redirects=False)
    return ParallelSearch(key, client=http), rec


def ok(_req):
    return httpx.Response(200, json=FIXTURE)


def test_request_selects_fast_with_bounded_results_and_no_extra_fields():
    api, rec = client(ok)
    objective, queries = build_search("CBC Cologne Business Center GmbH", title="Data Engineer (m/w/d)", city="Köln")
    res = api.search(objective, queries)
    req = rec.requests[0]
    body = json.loads(req.content)
    assert req.method == "POST" and str(req.url) == "https://api.parallel.ai/v1/search"
    assert req.headers["x-api-key"] == "pk-secret-1234567890"
    assert body["mode"] == "fast"
    assert body["advanced_settings"] == {"max_results": 10}
    assert body["max_chars_total"] == 6000
    assert set(body) == {"mode", "objective", "search_queries", "max_chars_total", "advanced_settings"}
    assert all(3 <= len(q.split()) <= 9 or len(q.split()) == 2 for q in body["search_queries"])
    assert [h.url for h in res.results] == ["https://www.cbc-cologne.de/karriere", "https://www.kununu.com/de/cbc-cologne"]
    assert res.results[0].title.startswith("Karriere") and res.results[1].title is None
    assert res.warning_texts == ["excerpts truncated"]


def test_hints_are_public_only():
    dirty = "Acme GmbH max.mustermann@example.com https://www.linkedin.com/comm/jobs/view/1/?trackingId=AbCdEf0123456789AbCdEf0123456789 +49 170 1234567"
    assert sanitize_hint(dirty) == "Acme GmbH"
    objective, queries = build_search("Acme GmbH", title="Dev (m/w/d) 4475748891abcdefghijklmnopqrstuvwxyz", city="Köln")
    text = objective + " ".join(queries)
    assert "@" not in text and "linkedin" not in text.lower() and "4475748891abcdef" not in text
    assert text.count("(m/w/d)") == 1 and '"Dev (m/w/d)"' in text  # only in the quoted ad title


def test_refined_query_is_different():
    _, first = build_search("Acme GmbH", title="Data Engineer", city="Köln")
    _, second = build_search("Acme GmbH", title="Data Engineer", city="Köln", refined=True)
    assert set(first) != set(second)


def test_missing_key_never_sends():
    api, rec = client(ok, key="")
    with pytest.raises(SearchNoKey):
        api.search("o", ["a b c"])
    assert rec.requests == []


@pytest.mark.parametrize(("status", "exc"), [(401, SearchAuthError), (403, SearchAuthError),
                                             (429, SearchRateLimited), (500, SearchUnavailable),
                                             (503, SearchUnavailable), (422, SearchMalformed)])
def test_status_codes_map_to_distinct_errors_without_retry(status, exc):
    api, rec = client(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(exc):
        api.search("o", ["a b c"])
    assert len(rec.requests) == 1


def test_timeout_and_garbage_are_distinct():
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)
    api, rec = client(boom)
    with pytest.raises(SearchUnavailable):
        api.search("o", ["a b c"])
    api, _ = client(lambda r: httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(SearchMalformed):
        api.search("o", ["a b c"])
    api, _ = client(lambda r: httpx.Response(200, json={"results": "not a list"}))
    with pytest.raises(SearchMalformed):
        api.search("o", ["a b c"])


def test_redirect_is_refused_and_key_never_reaches_the_target():
    def redirect(request):
        if request.url.host == "api.parallel.ai":
            return httpx.Response(307, headers={"Location": "https://evil.example/collect"})
        return httpx.Response(200, json=FIXTURE)
    api, rec = client(redirect)
    with pytest.raises(SearchRedirected):
        api.search("o", ["a b c"])
    assert [r.url.host for r in rec.requests] == ["api.parallel.ai"]


# --- gateway: caps, ceilings, failures ---------------------------------------------------------


def make_gateway(tmp_path, handler=ok, *, key="pk-x", **limits):
    conn = open_db(tmp_path / "a.db")
    cfg = EmployerCareersConfig(enabled=True, **limits)
    api, rec = client(handler, key=key)
    return SearchGateway(conn, cfg, api), rec, conn, cfg


def test_two_searches_per_employer_then_stop(tmp_path):
    gw, rec, *_ = make_gateway(tmp_path)
    gw.search("emp-1", "o", ["a b c"])
    gw.search("emp-1", "o", ["d e f"])
    with pytest.raises(SearchUnavailableForRun) as err:
        gw.search("emp-1", "o", ["g h i"])
    assert err.value.kind == "employer_limit" and len(rec.requests) == 2
    gw.search("emp-2", "o", ["a b c"])
    assert len(rec.requests) == 3


def test_failed_search_keeps_its_reservation_and_is_not_retried(tmp_path):
    gw, rec, conn, _ = make_gateway(tmp_path, lambda r: httpx.Response(503))
    with pytest.raises(SearchUnavailable):
        gw.search("emp-1", "o", ["a b c"])
    assert gw.usage.requests == 1 and gw.usage.failures == 1 and len(rec.requests) == 1
    assert conn.execute("SELECT SUM(requests) FROM api_usage WHERE source = 'parallel-search'").fetchone()[0] == 1


def test_caps_persist_across_fresh_contexts_at_exact_boundaries(tmp_path):
    path = tmp_path / "a.db"
    cfg = EmployerCareersConfig(enabled=True, max_search_requests_per_run=2, max_search_requests_per_day=3,
                                max_search_requests_per_month=10)
    for expected_sent in (2, 1, 0):  # run 1 sends 2 (run cap), run 2 sends 1 (day cap), run 3 sends none
        conn = open_db(path)
        api, rec = client(ok)
        gw = SearchGateway(conn, cfg, api)
        sent = 0
        for n in range(4):
            try:
                gw.search(f"emp-{n}", "o", ["a b c"])
                sent += 1
            except SearchUnavailableForRun as err:
                assert err.kind == "cap"
        assert sent == expected_sent
        conn.close()


def test_auth_failure_stops_later_calls_but_not_the_run(tmp_path):
    gw, rec, *_ = make_gateway(tmp_path, lambda r: httpx.Response(401))
    with pytest.raises(SearchAuthError):
        gw.search("emp-1", "o", ["a b c"])
    with pytest.raises(SearchUnavailableForRun) as err:
        gw.search("emp-2", "o", ["a b c"])
    assert err.value.kind == "auth" and len(rec.requests) == 1


def test_rate_limit_stops_the_run(tmp_path):
    gw, rec, *_ = make_gateway(tmp_path, lambda r: httpx.Response(429))
    with pytest.raises(SearchRateLimited):
        gw.search("emp-1", "o", ["a b c"])
    with pytest.raises(SearchUnavailableForRun) as err:
        gw.search("emp-2", "o", ["a b c"])
    assert err.value.kind == "rate_limited"


def test_missing_key_is_a_visible_stop_with_no_request(tmp_path):
    gw, rec, *_ = make_gateway(tmp_path, key="")
    assert gw.stop_kind == "no_key" and "PARALLEL_API_KEY" in gw.stop_reason
    with pytest.raises(SearchUnavailableForRun):
        gw.search("emp-1", "o", ["a b c"])
    assert rec.requests == []


def test_usage_estimate_is_gross_and_separate_from_llm_cost(tmp_path):
    gw, *_ = make_gateway(tmp_path)
    gw.search("emp-1", "o", ["a b c"])
    gw.search("emp-2", "o", ["a b c"])
    assert gw.usage.requests == 2
    assert gw.usage.estimated_usd == pytest.approx(0.002)
    assert gw.usage.warnings == ["excerpts truncated", "excerpts truncated"]
