import logging

import httpx

from autoapp.sources.stepstone_links import (
    StepStoneLinkResolver,
    canonical_job_url,
    job_id_from_location,
)

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJjaWQiOiJzZWNyZXQifQ.c2lnbmF0dXJl"
LOGIN = (
    f"https://www.stepstone.de/v2/magiclink/exchange?magicLink={TOKEN}"
    "&returnUrl=/stellenangebote----13850318-inline.html%3FCID%3DR_EM_JaJob%26lang%3Dde&offer_position=1"
)
LINK = "https://click.stepstone.de/f/a/DUMMY1~~/AAAA~/DUMMY1"


def _resolver(handler, requests):
    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record), follow_redirects=False)
    return StepStoneLinkResolver(client, delay_s=0)


def test_normal_redirect_gives_job_id_and_never_touches_login(caplog):
    requests: list[httpx.Request] = []
    r = _resolver(lambda req: httpx.Response(302, headers={"location": LOGIN}), requests)
    with caplog.at_level(logging.DEBUG):
        res = r.resolve(LINK)
    assert res.ok and res.job_id == "13850318"
    assert res.url == canonical_job_url("13850318") == "https://www.stepstone.de/stellenangebote--13850318-inline.html"
    assert [str(q.url) for q in requests] == [LINK]
    assert not any("/v2/magiclink/" in str(q.url) for q in requests)
    assert TOKEN not in repr(res)
    assert "magicLink" not in caplog.text and TOKEN not in caplog.text


def test_missing_return_url():
    r = _resolver(lambda req: httpx.Response(302, headers={"location": "https://www.stepstone.de/"}), [])
    res = r.resolve(LINK)
    assert not res.ok and "no job ID" in res.error


def test_not_found():
    r = _resolver(lambda req: httpx.Response(404), [])
    res = r.resolve(LINK)
    assert not res.ok and "404" in res.error


def test_error_message_never_contains_token():
    r = _resolver(lambda req: httpx.Response(302, headers={"location": LOGIN.replace("13850318-inline", "x")}), [])
    res = r.resolve(LINK)
    assert not res.ok and TOKEN not in (res.error or "")


def test_only_tracking_hosts_are_requested():
    requests: list[httpx.Request] = []
    r = _resolver(lambda req: httpx.Response(200), requests)
    res = r.resolve("https://evil.example.com/redirect")
    assert not res.ok and requests == []


def test_direct_job_redirect_supported():
    loc = "https://www.stepstone.de/stellenangebote--Data-Engineer-Muster--13850318-inline.html?x=1"
    assert job_id_from_location(loc) == "13850318"
    assert job_id_from_location(None) is None


def test_pacing_between_requests():
    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(302, headers={"location": LOGIN})))
    r = StepStoneLinkResolver(client, delay_s=1.5, sleep=slept.append)
    r.resolve(LINK)
    r.resolve(LINK)
    assert slept == [1.5]
