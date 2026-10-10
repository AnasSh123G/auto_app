"""Polite plain-HTTP page fetcher for employer and ATS pages and Adzuna redirects."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel

USER_AGENT = "autoapp/0.1 (personal job search tool)"
BLOCK_STATUSES = frozenset({403, 429, 999})
# Content types that can be read as a page; anything else (PDF, images, archives) is never decoded as text.
TEXT_TYPES = ("text/", "application/xhtml", "application/xml", "application/rss", "application/atom",
              "application/json", "application/ld+json")


def is_text_response(content_type: str | None) -> bool:
    """True for a readable page. A missing header counts as readable (the body is decoded as before)."""
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    return not kind or kind.startswith(TEXT_TYPES) or kind.endswith(("+xml", "+json"))


class FetchResult(BaseModel):
    """Outcome of one page fetch. ``blocked`` means the host refused us and is now off for this run."""

    url: str
    status: int
    html: str | None = None
    final_url: str | None = None
    blocked: bool = False
    rejected: bool = False
    reason: str | None = None
    redirect_url: str | None = None

    @property
    def ok(self) -> bool:
        return self.html is not None and not self.blocked


class HttpFetcher:
    """Fetches pages with an honest User-Agent and a randomised delay per host.

    A 403/429 (or 999) response disables that host for the rest of the run; nothing is retried
    or worked around.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        min_delay_s: float = 2.0,
        max_delay_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        if max_delay_s < min_delay_s:
            raise ValueError("max_delay_s must be >= min_delay_s")
        self._client = client or httpx.Client(timeout=30, follow_redirects=False)
        self._min = min_delay_s
        self._max = max_delay_s
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._last_request: dict[str, float] = {}
        self._disabled: dict[str, str] = {}
        self._cache: dict[str, FetchResult] = {}
        # Told (host, seconds) before every pause between two requests to the same host.
        self.on_wait: Callable[[str, float], None] | None = None

    @staticmethod
    def host_of(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    def is_disabled(self, url_or_host: str) -> bool:
        host = self.host_of(url_or_host) if "/" in url_or_host else url_or_host.lower()
        return host in self._disabled

    @property
    def disabled_hosts(self) -> dict[str, str]:
        """Hosts switched off for this run, with the reason."""
        return dict(self._disabled)

    def _wait_for(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is None:
            return
        wanted = self._rng.uniform(self._min, self._max)
        remaining = wanted - (self._clock() - last)
        if remaining > 0:
            if self.on_wait is not None:
                self.on_wait(host, remaining)
            self._sleep(remaining)

    def close(self) -> None:
        self._client.close()

    def fetch_once(self, url: str, *, check: Callable[[str], str | None] | None = None,
                   client: httpx.Client | None = None) -> FetchResult:
        """One cached GET; redirects are returned for the caller to validate.

        ``check`` (address -> reason to refuse, or None) runs before anything else, and ``client`` replaces
        the default HTTP client for this call; cache, pacing and host blocks stay shared. Both default to
        the historical behaviour.
        """
        url = urlsplit(url)._replace(fragment="").geturl()
        if check is not None and (reason := check(url)):
            return FetchResult(url=url, status=0, rejected=True, reason=reason)
        if urlsplit(url).scheme not in ("http", "https") or not self.host_of(url):
            return FetchResult(url=url, status=0, reason="unsupported or missing HTTP address")
        if url in self._cache:
            return self._cache[url].model_copy()
        host = self.host_of(url)
        if host in self._disabled:
            return FetchResult(url=url, status=0, blocked=True, reason=f"{host} disabled for this run: {self._disabled[host]}")
        self._wait_for(host)
        try:
            resp = (client or self._client).get(
                url, follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            self._last_request[host] = self._clock()
            result = FetchResult(url=url, status=0, reason=f"request failed: {exc.__class__.__name__}")
            self._cache[url] = result
            return result.model_copy()
        self._last_request[host] = self._clock()
        final = str(resp.url)
        if resp.status_code in BLOCK_STATUSES:
            reason = f"HTTP {resp.status_code}"
            self._disabled[host] = reason
            result = FetchResult(url=url, status=resp.status_code, final_url=final, blocked=True, reason=reason)
        elif resp.is_redirect and resp.headers.get("Location"):
            result = FetchResult(
                url=url, status=resp.status_code, final_url=final,
                redirect_url=urljoin(final, resp.headers["Location"]),
            )
        elif not resp.is_success:
            result = FetchResult(url=url, status=resp.status_code, final_url=final, reason=f"HTTP {resp.status_code}")
        elif not is_text_response(resp.headers.get("Content-Type")):
            kind = resp.headers.get("Content-Type", "").split(";", 1)[0].strip()
            result = FetchResult(url=url, status=resp.status_code, final_url=final, reason=f"not a web page ({kind})")
        else:
            result = FetchResult(url=url, status=resp.status_code, html=resp.text, final_url=final)
        self._cache[url] = result
        return result.model_copy()

    def fetch(self, url: str) -> FetchResult:
        """Read a detail page using explicit, paced redirects (the historical 20-redirect cap)."""
        visited: set[str] = set()
        current = url
        for _ in range(21):
            address = urlsplit(current)._replace(fragment="").geturl()
            if address in visited:
                return FetchResult(url=url, status=0, final_url=current, reason="redirect loop")
            visited.add(address)
            result = self.fetch_once(address)
            if result.redirect_url is None:
                return result.model_copy(update={"url": url})
            current = result.redirect_url
        return FetchResult(url=url, status=0, final_url=result.final_url, reason="too many HTTP redirects")
