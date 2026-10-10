"""Public-target guard (task 3.3): zero requests reach private or restricted destinations."""

from __future__ import annotations

import socket
import threading

import httpx
import pytest

from autoapp.config.models import DEFAULT_RESTRICTED_DOMAINS
from autoapp.employers.guard import GuardedTransport, check_target, guarded_client, is_public_address
from autoapp.fetch.http_fetcher import HttpFetcher

PUBLIC = "93.184.216.34"


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        if host not in mapping:
            raise socket.gaierror("unknown host")
        return mapping[host]
    return resolve


R = resolver_for({
    "careers.example.com": [PUBLIC], "www.example.com": [PUBLIC],
    "127.0.0.1.nip.io": ["127.0.0.1"], "mixed.example": [PUBLIC, "10.0.0.7"],
    "internal-app.example": ["192.168.1.20"], "meta.example": ["169.254.169.254"],
    "www.linkedin.com": [PUBLIC], "uk.linkedin.com": [PUBLIC], "de.indeed.com": [PUBLIC],
    "jobs.stepstone.de": [PUBLIC],
})


@pytest.mark.parametrize("url", [
    "https://careers.example.com/jobs", "http://www.example.com/", "https://careers.example.com/a?b=1#x",
])
def test_public_addresses_are_allowed(url):
    assert check_target(url, DEFAULT_RESTRICTED_DOMAINS, R) is None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://[::1]/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.9/",
    "http://169.254.169.254/latest/meta-data", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/",
    "http://localhost/", "http://printer.local/", "http://intranet/", "http://2130706433/",
    "http://127.0.0.1.nip.io/", "https://mixed.example/", "https://internal-app.example/",
    "https://meta.example/", "https://unknown-host.example/",
])
def test_private_and_deceptive_hosts_are_rejected(url):
    assert check_target(url, DEFAULT_RESTRICTED_DOMAINS, R) is not None


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://careers.example.com/", "javascript:alert(1)", "data:text/html,hi",
    "https://user:pw@careers.example.com/", "https://careers.example.com@evil.example/",
    "http://careers.example.com:8080/", "https:///nohost",
])
def test_unsafe_schemes_credentials_and_odd_ports_are_rejected(url):
    assert check_target(url, DEFAULT_RESTRICTED_DOMAINS, R) is not None


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/1", "https://uk.linkedin.com/x", "https://de.indeed.com/viewjob",
    "https://jobs.stepstone.de/a",
])
def test_restricted_platforms_and_subdomains_are_rejected(url):
    reason = check_target(url, DEFAULT_RESTRICTED_DOMAINS, R)
    assert reason and "restricted" in reason


def test_is_public_address():
    assert is_public_address(PUBLIC) and not is_public_address("127.0.0.1") and not is_public_address("fe80::1")
    assert not is_public_address("not-an-ip")


def test_fetcher_hook_sends_nothing_for_rejected_targets_including_redirects():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "careers.example.com":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
        return httpx.Response(200, text="<html>ok</html>")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)), min_delay_s=0, max_delay_s=0)
    guard = lambda u: check_target(u, DEFAULT_RESTRICTED_DOMAINS, R)  # noqa: E731
    first = fetcher.fetch_once("https://careers.example.com/jobs", check=guard)
    assert first.redirect_url == "http://127.0.0.1/admin"
    second = fetcher.fetch_once(first.redirect_url, check=guard)
    assert second.rejected and "non-public" in (second.reason or "")
    third = fetcher.fetch_once("https://www.linkedin.com/jobs/view/1", check=guard)
    assert third.rejected
    assert [r.url.host for r in seen] == ["careers.example.com"]


def test_default_fetch_once_behaviour_is_unchanged():
    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="hi"))),
                          min_delay_s=0, max_delay_s=0)
    res = fetcher.fetch_once("http://127.0.0.1/x")
    assert res.ok and not res.rejected


def test_dns_rebinding_never_connects():
    """The pre-check sees a public address; at connection time the name resolves to loopback."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    accepted: list[int] = []

    def serve() -> None:
        server.settimeout(1.5)
        try:
            while True:
                conn, _ = server.accept()
                accepted.append(1)
                conn.close()
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    answers = iter([[PUBLIC], ["127.0.0.1"], ["127.0.0.1"]])
    rebinding = lambda host: next(answers)  # noqa: E731
    url = f"http://rebind.example:{port}/"
    assert check_target(url.replace(f":{port}", ""), DEFAULT_RESTRICTED_DOMAINS, rebinding) is None
    client = httpx.Client(transport=GuardedTransport(rebinding), trust_env=False)
    with pytest.raises(httpx.ConnectError):
        client.get(url)
    thread.join()
    server.close()
    assert accepted == []


def test_guarded_client_does_not_use_proxies_from_the_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    client = guarded_client(resolver_for({"careers.example.com": ["127.0.0.1"]}))
    with pytest.raises(httpx.ConnectError):  # refused by the guard, not routed to the proxy
        client.get("http://careers.example.com/")
