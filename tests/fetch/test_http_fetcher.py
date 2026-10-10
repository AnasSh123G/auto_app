from __future__ import annotations

import httpx

from autoapp.fetch.http_fetcher import USER_AGENT, HttpFetcher


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(handler, min_delay: float = 3.0, max_delay: float = 3.0):
    clk = FakeClock()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return HttpFetcher(client, min_delay, max_delay, sleep=clk.sleep, clock=clk.clock), clk


def test_normal_fetch_sends_honest_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="<html>ok</html>")

    fetcher, _ = make(handler)
    res = fetcher.fetch("https://jobs.example.com/a")
    assert res.ok and res.status == 200 and res.html == "<html>ok</html>"
    assert res.final_url == "https://jobs.example.com/a"
    assert seen[0].headers["User-Agent"] == USER_AGENT == "autoapp/0.1 (personal job search tool)"


def test_final_url_after_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r":
            return httpx.Response(302, headers={"Location": "https://jobs.example.com/real"})
        return httpx.Response(200, text="x")

    fetcher, _ = make(handler)
    assert fetcher.fetch("https://jobs.example.com/r").final_url == "https://jobs.example.com/real"


def test_per_host_delay_only_between_requests_to_same_host() -> None:
    fetcher, clk = make(lambda r: httpx.Response(200, text="x"), 3.0, 3.0)
    fetcher.fetch("https://a.example.com/1")
    assert clk.sleeps == []
    fetcher.fetch("https://b.example.com/1")  # other host: no wait
    assert clk.sleeps == []
    fetcher.fetch("https://a.example.com/2")
    assert clk.sleeps == [3.0]
    clk.now += 10  # already waited long enough
    fetcher.fetch("https://a.example.com/3")
    assert clk.sleeps == [3.0]


def test_403_blocks_and_disables_host_for_the_run() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(403 if request.url.host == "blocked.example.com" else 200, text="x")

    fetcher, _ = make(handler)
    first = fetcher.fetch("https://blocked.example.com/a")
    assert first.blocked and first.status == 403 and first.html is None and first.reason == "HTTP 403"
    second = fetcher.fetch("https://blocked.example.com/b")
    assert second.blocked and second.status == 0
    assert calls == ["https://blocked.example.com/a"]  # second never hit the network
    assert fetcher.is_disabled("https://blocked.example.com/z")
    assert fetcher.fetch("https://ok.example.com/").ok


def test_429_blocks() -> None:
    fetcher, _ = make(lambda r: httpx.Response(429, headers={"Retry-After": "60"}))
    res = fetcher.fetch("https://a.example.com/")
    assert res.blocked and res.status == 429
    assert fetcher.disabled_hosts == {"a.example.com": "HTTP 429"}


def test_404_is_not_a_block() -> None:
    fetcher, _ = make(lambda r: httpx.Response(404, text="nope"))
    res = fetcher.fetch("https://a.example.com/x")
    assert not res.blocked and res.html is None and res.status == 404
    assert not fetcher.is_disabled("a.example.com")


def test_network_error_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    fetcher, _ = make(handler)
    res = fetcher.fetch("https://a.example.com/x")
    assert res.status == 0 and not res.blocked and res.reason and "ConnectError" in res.reason


def test_single_response_never_follows_injected_client_redirects():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://www.linkedin.com/jobs/1"})

    fetcher, _ = make(handler)
    result = fetcher.fetch_once("https://jobs.example.com/start")
    assert result.redirect_url == "https://www.linkedin.com/jobs/1"
    assert not result.ok
    assert seen == ["https://jobs.example.com/start"]


def test_redirects_are_cached_paced_and_block_actual_destination():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/next"})
        if request.url.path == "/next":
            return httpx.Response(302, headers={"Location": "https://blocked.example.com/end"})
        return httpx.Response(403)

    fetcher, clock = make(handler)
    result = fetcher.fetch("https://jobs.example.com/start")
    assert result.blocked
    assert fetcher.disabled_hosts == {"blocked.example.com": "HTTP 403"}
    assert clock.sleeps == [3.0]
    fetcher.fetch("https://jobs.example.com/start")
    assert len(seen) == 3
    assert fetcher.fetch_once("https://blocked.example.com/another").blocked
    assert len(seen) == 3


def test_network_failures_are_cached():
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ConnectError("offline")

    fetcher, _ = make(handler)
    fetcher.fetch_once("https://jobs.example.com/start")
    fetcher.fetch_once("https://jobs.example.com/start#top")
    assert len(seen) == 1


def test_detail_redirect_loop_stops_without_repeating_request():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "/start"})

    fetcher, _ = make(handler)
    assert fetcher.fetch("https://jobs.example.com/start").reason == "redirect loop"
    assert len(seen) == 1
