from __future__ import annotations

import random
from pathlib import Path

import pytest

from autoapp.fetch.browser_fetcher import BrowserFetcher, is_challenge, site_key

PAGES = Path(__file__).resolve().parent.parent / "fixtures" / "pages"


# ------------------------------------------------------------------- detector (no browser)


@pytest.mark.parametrize("status", [403, 429, 999])
def test_challenge_by_status(status: int) -> None:
    assert is_challenge(status, "Whatever", "<html>" + "x" * 50_000 + "</html>")


@pytest.mark.parametrize(
    "title",
    ["Just a moment...", "Security Check", "Attention Required! | Cloudflare", "Access Denied", "Captcha", "Please Verify You Are Human"],
)
def test_challenge_by_title(title: str) -> None:
    assert is_challenge(200, title, "<html></html>")


def test_challenge_by_html_markers() -> None:
    assert is_challenge(200, "Page", '<div id="px-captcha"></div>')
    assert is_challenge(200, "Page", '<div id="cf-challenge-running"></div>')
    assert is_challenge(200, "Page", '<div class="g-recaptcha"></div>')  # small page with a CAPTCHA widget


def test_big_normal_page_with_recaptcha_widget_is_not_a_challenge() -> None:
    html = "<html><body>" + "job text " * 6000 + '<div class="g-recaptcha"></div></body></html>'
    assert not is_challenge(200, "Senior Data Scientist", html)


def test_normal_page_is_not_a_challenge() -> None:
    assert not is_challenge(200, "Data Scientist - Acme", (PAGES / "normal_job.html").read_text(encoding="utf-8"))
    assert not is_challenge(404, "Not found", "<html>gone</html>")
    assert not is_challenge(None, None, None)


def test_fixture_challenge_pages_are_detected() -> None:
    assert is_challenge(200, "Just a moment...", (PAGES / "challenge_cloudflare.html").read_text(encoding="utf-8"))
    assert is_challenge(200, "Security check", (PAGES / "challenge_captcha.html").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://www.linkedin.com/jobs/view/1/", "linkedin"),
        ("https://de.linkedin.com/jobs/view/1/", "linkedin"),
        ("https://de.indeed.com/viewjob?jk=1", "indeed"),
        ("https://www.indeed.de/viewjob?jk=1", "indeed"),
        ("https://www.stepstone.de/stellenangebote--1-inline.html", "stepstone"),
        ("https://karriere.example.com/x", "karriere.example.com"),
    ],
)
def test_site_key(url: str, site: str) -> None:
    assert site_key(url) == site


# ------------------------------------------------------- limits that need no browser launch


def test_disabled_site_short_circuits_without_launching(tmp_path: Path) -> None:
    fetcher = BrowserFetcher(tmp_path / "p", enabled={"linkedin": False})
    res = fetcher.fetch("https://www.linkedin.com/jobs/view/1/")
    assert not res.blocked and res.html is None and "disabled" in (res.reason or "")
    assert fetcher._context is None  # nothing launched


def test_zero_cap_short_circuits_without_launching(tmp_path: Path) -> None:
    fetcher = BrowserFetcher(tmp_path / "p", max_per_site=0)
    res = fetcher.fetch("https://www.stepstone.de/x")
    assert res.html is None and "cap" in (res.reason or "")
    assert fetcher._context is None


# --------------------------------------------------------------------- real Chromium

PAGES_BY_URL = {
    "https://www.stepstone.de/stellenangebote--1-inline.html": (200, "normal_job.html"),
    "https://www.stepstone.de/stellenangebote--2-inline.html": (200, "normal_job.html"),
    "https://www.stepstone.de/stellenangebote--3-inline.html": (200, "normal_job.html"),
    "https://www.linkedin.com/jobs/view/1/": (200, "challenge_cloudflare.html"),
    "https://www.linkedin.com/jobs/view/2/": (200, "normal_job.html"),
    "https://de.indeed.com/viewjob?jk=1": (429, "challenge_captcha.html"),
    "https://de.indeed.com/viewjob?jk=2": (200, "normal_job.html"),
    "https://www.stepstone.de/missing": (404, "empty.html"),
}


def install_routes(context) -> None:
    def handler(route) -> None:
        entry = PAGES_BY_URL.get(route.request.url)
        if entry is None:
            route.abort()  # never touch the real network
            return
        status, name = entry
        route.fulfill(status=status, content_type="text/html; charset=utf-8", body=(PAGES / name).read_text(encoding="utf-8"))

    context.route("**/*", handler)


@pytest.fixture
def fetcher(tmp_path: Path):
    f = BrowserFetcher(
        tmp_path / "profile",
        max_per_site=2,
        min_delay_s=0,
        max_delay_s=0,
        sleep=lambda s: None,
        rng=random.Random(0),
        on_context=install_routes,
    )
    yield f
    f.close()


@pytest.mark.browser
def test_normal_page_returns_html(fetcher: BrowserFetcher) -> None:
    res = fetcher.fetch("https://www.stepstone.de/stellenangebote--1-inline.html")
    assert res.ok and res.status == 200
    assert res.html and "Test Engineer" in res.html
    assert res.final_url == "https://www.stepstone.de/stellenangebote--1-inline.html"
    assert fetcher.blocked_sites == {}


@pytest.mark.browser
def test_challenge_blocks_site_and_short_circuits_second_fetch(fetcher: BrowserFetcher) -> None:
    first = fetcher.fetch("https://www.linkedin.com/jobs/view/1/")
    assert first.blocked and first.html is None and "challenge" in (first.reason or "")
    assert "linkedin" in fetcher.blocked_sites
    second = fetcher.fetch("https://www.linkedin.com/jobs/view/2/")  # would be a normal page
    assert second.blocked and second.status == 0 and second.html is None
    # other sites are unaffected
    assert fetcher.fetch("https://www.stepstone.de/stellenangebote--1-inline.html").ok


@pytest.mark.browser
def test_http_429_blocks_site(fetcher: BrowserFetcher) -> None:
    res = fetcher.fetch("https://de.indeed.com/viewjob?jk=1")
    assert res.blocked and res.status == 429
    assert fetcher.fetch("https://de.indeed.com/viewjob?jk=2").blocked


@pytest.mark.browser
def test_per_site_cap(fetcher: BrowserFetcher) -> None:
    assert fetcher.fetch("https://www.stepstone.de/stellenangebote--1-inline.html").ok
    assert fetcher.fetch("https://www.stepstone.de/stellenangebote--2-inline.html").ok
    third = fetcher.fetch("https://www.stepstone.de/stellenangebote--3-inline.html")
    assert third.html is None and not third.blocked and "cap" in (third.reason or "")


@pytest.mark.browser
def test_404_is_not_a_block(fetcher: BrowserFetcher) -> None:
    res = fetcher.fetch("https://www.stepstone.de/missing")
    assert res.status == 404 and res.html is None and not res.blocked
    assert fetcher.blocked_sites == {}
