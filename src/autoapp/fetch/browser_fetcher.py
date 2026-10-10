"""Playwright page fetcher for linkedin.com, indeed.* and stepstone.de job pages.

Sequential, headless, persistent profile, randomised delays, a per-site cap. On a bot challenge
or login wall the site is switched off for the rest of the run. It never solves or bypasses
anything.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from autoapp.fetch.http_fetcher import FetchResult

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Playwright

    from autoapp.config.models import DetailFetch

BLOCK_STATUSES = frozenset({403, 429, 999})

_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "security check",
    "captcha",
    "verify you are human",
    "verify you are a human",
    "are you a robot",
    "robot or human",
    "access denied",
    "pardon our interruption",
    "bot verification",
    "additional verification required",
    "sicherheitsüberprüfung",
    "sicherheitsabfrage",
)
# Present only on challenge pages, so safe on pages of any size.
_STRONG_HTML_MARKERS = ("cf-challenge-running", "cf-browser-verification", "px-captcha", "challenge-form", "verify you are human")
# Also appear on normal pages with embedded forms, so only count on small pages.
_WEAK_HTML_MARKERS = ("captcha", "recaptcha", "hcaptcha", "just a moment", "security check", "access denied")
_SMALL_PAGE_CHARS = 30_000
_LOGIN_WALL_PATH = re.compile(r"^/(authwall|checkpoint|uas/login|login)\b")


def is_challenge(status: int | None, title: str | None, html: str | None) -> bool:
    """True for a bot-challenge page: HTTP 403/429/999, or a CAPTCHA / "Just a moment" style page."""
    if status in BLOCK_STATUSES:
        return True
    low_title = (title or "").lower()
    if any(m in low_title for m in _TITLE_MARKERS):
        return True
    low_html = (html or "").lower()
    if any(m in low_html for m in _STRONG_HTML_MARKERS):
        return True
    return len(low_html) < _SMALL_PAGE_CHARS and any(m in low_html for m in _WEAK_HTML_MARKERS)


def site_key(url: str) -> str:
    """``linkedin``, ``indeed``, ``stepstone`` for the known platforms, else the hostname."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    if re.search(r"(^|\.)indeed\.[a-z.]+$", host):
        return "indeed"
    if host == "stepstone.de" or host.endswith(".stepstone.de"):
        return "stepstone"
    return host


class BrowserFetcher:
    """Fetches job pages through a persistent headless Chromium profile, one page at a time."""

    def __init__(
        self,
        profile_dir: Path | str,
        *,
        enabled: Mapping[str, bool] | None = None,
        max_per_site: int = 20,
        min_delay_s: float = 20.0,
        max_delay_s: float = 40.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
        on_context: Callable[[BrowserContext], None] | None = None,
    ) -> None:
        if max_delay_s < min_delay_s:
            raise ValueError("max_delay_s must be >= min_delay_s")
        self._profile_dir = Path(profile_dir)
        self._enabled = dict(enabled or {})
        self._max_per_site = max_per_site
        self._min = min_delay_s
        self._max = max_delay_s
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._on_context = on_context  # test hook, e.g. to install page.route handlers
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._counts: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self._blocked: dict[str, str] = {}
        # Told (site, seconds) before every pause between two pages of the same site.
        self.on_wait: Callable[[str, float], None] | None = None

    @classmethod
    def from_config(cls, detail_fetch: DetailFetch, profile_dir: Path | str, **kwargs: Any) -> BrowserFetcher:
        """Build from the ``detail_fetch`` config section."""
        return cls(
            profile_dir,
            enabled={
                "linkedin": detail_fetch.linkedin,
                "indeed": detail_fetch.indeed,
                "stepstone": detail_fetch.stepstone,
            },
            max_per_site=detail_fetch.max_per_site,
            min_delay_s=detail_fetch.min_delay_s,
            max_delay_s=detail_fetch.max_delay_s,
            **kwargs,
        )

    # ------------------------------------------------------------------ state

    @property
    def blocked_sites(self) -> dict[str, str]:
        """Sites switched off for this run because of a challenge, with the reason."""
        return dict(self._blocked)

    def is_enabled(self, site: str) -> bool:
        return self._enabled.get(site, True) and site not in self._blocked

    # --------------------------------------------------------------- lifecycle

    def _ensure_context(self) -> BrowserContext:
        if self._context is None:
            from playwright.sync_api import sync_playwright

            self._profile_dir.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            self._context = self._pw.chromium.launch_persistent_context(
                str(self._profile_dir), headless=self._headless, locale="de-DE"
            )
            if self._on_context:
                self._on_context(self._context)
        return self._context

    def close(self) -> None:
        """Close the browser; safe to call twice."""
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def __enter__(self) -> BrowserFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------- fetch

    def _block(self, site: str, url: str, status: int, reason: str, final_url: str | None = None) -> FetchResult:
        self._blocked[site] = reason
        return FetchResult(url=url, status=status, final_url=final_url, blocked=True, reason=reason)

    def fetch(self, url: str) -> FetchResult:
        """Load ``url``. Returns blocked (and disables the site) on a challenge or login wall."""
        site = site_key(url)
        if site in self._blocked:
            return FetchResult(
                url=url, status=0, blocked=True, reason=f"{site} disabled for this run: {self._blocked[site]}"
            )
        if not self._enabled.get(site, True):
            return FetchResult(url=url, status=0, reason=f"detail fetch disabled for {site}")
        if self._counts.get(site, 0) >= self._max_per_site:
            return FetchResult(url=url, status=0, reason=f"per-site cap reached ({self._max_per_site}) for {site}")

        last = self._last.get(site)
        if last is not None:
            remaining = self._rng.uniform(self._min, self._max) - (self._clock() - last)
            if remaining > 0:
                if self.on_wait is not None:
                    self.on_wait(site, remaining)
                self._sleep(remaining)
        self._counts[site] = self._counts.get(site, 0) + 1

        context = self._ensure_context()
        page = context.new_page()
        try:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                status = response.status if response is not None else 0
                final_url = page.url
                title = page.title()
                html = page.content()
            except Exception as exc:  # Playwright timeouts and navigation errors
                return FetchResult(url=url, status=0, reason=f"navigation failed: {exc.__class__.__name__}")
        finally:
            page.close()
            self._last[site] = self._clock()

        if is_challenge(status, title, html):
            return self._block(site, url, status, f"bot challenge (HTTP {status}, title {title!r})", final_url)
        if _LOGIN_WALL_PATH.match(urlsplit(final_url).path or ""):
            return self._block(site, url, status, "login wall", final_url)
        if not 200 <= status < 300:
            return FetchResult(url=url, status=status, final_url=final_url, reason=f"HTTP {status}")
        return FetchResult(url=url, status=status, html=html, final_url=final_url)
