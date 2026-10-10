"""Typed client for Parallel's ``POST /v1/search`` (explicit Fast mode, bounded results).

Contract (https://docs.parallel.ai/api-reference/search/search, checked 2026-10): header
``x-api-key``; body ``mode``, ``objective``, ``search_queries``, ``max_chars_total`` and
``advanced_settings.max_results``; response ``results[{url, title?, publish_date?, excerpts[]}]``,
``warnings[]``, ``usage[{name, count}]``, ``search_id``. The key goes only to this endpoint:
redirects are refused, so it can never reach another host.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENDPOINT = "https://api.parallel.ai/v1/search"
MODE = "fast"
MAX_RESULTS = 10
MAX_CHARS_TOTAL = 6000
SOURCE = "parallel-search"


class SearchError(Exception):
    """Base class; ``kind`` is the stable reason shown in reports."""

    kind = "error"


class SearchNoKey(SearchError):
    kind = "no_key"


class SearchAuthError(SearchError):
    kind = "auth"


class SearchRateLimited(SearchError):
    kind = "rate_limited"


class SearchUnavailable(SearchError):
    kind = "unavailable"


class SearchMalformed(SearchError):
    kind = "malformed"


class SearchRedirected(SearchError):
    kind = "redirected"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SearchHit(_Lenient):
    url: str
    title: str | None = None
    excerpts: list[str] = Field(default_factory=list, max_length=20)


class SearchResponse(_Lenient):
    search_id: str | None = None
    results: list[SearchHit] = Field(default_factory=list)
    warnings: list[Any] | None = None

    @property
    def warning_texts(self) -> list[str]:
        out = []
        for w in self.warnings or []:
            out.append(str(w.get("message") or w.get("type") or w) if isinstance(w, dict) else str(w))
        return [t[:200] for t in out]


def build_request(objective: str, queries: list[str]) -> dict[str, Any]:
    return {
        "mode": MODE,
        "objective": objective,
        "search_queries": queries,
        "max_chars_total": MAX_CHARS_TOTAL,
        "advanced_settings": {"max_results": MAX_RESULTS},
    }


class ParallelSearch:
    """One HTTP call per ``search``; never retries, never follows redirects."""

    def __init__(self, api_key: str | None, *, client: httpx.Client | None = None, endpoint: str = ENDPOINT,
                 timeout: float = 20.0) -> None:
        self._key = (api_key or "").strip()
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._endpoint = endpoint

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def close(self) -> None:
        self._client.close()

    def search(self, objective: str, queries: list[str]) -> SearchResponse:
        if not self._key:
            raise SearchNoKey("PARALLEL_API_KEY is not set")
        try:
            resp = self._client.post(self._endpoint, json=build_request(objective, queries),
                                     headers={"x-api-key": self._key, "Content-Type": "application/json"},
                                     follow_redirects=False)
        except httpx.TimeoutException as err:
            raise SearchUnavailable("search timed out") from err
        except httpx.HTTPError as err:
            raise SearchUnavailable(f"search request failed: {err.__class__.__name__}") from err
        code = resp.status_code
        if 300 <= code < 400:
            raise SearchRedirected(f"search endpoint redirected (HTTP {code}); refusing to follow it")
        if code in (401, 403):
            raise SearchAuthError(f"search rejected the API key (HTTP {code})")
        if code == 429:
            raise SearchRateLimited("search rate limit reached (HTTP 429)")
        if code >= 500:
            raise SearchUnavailable(f"search service error (HTTP {code})")
        if code != 200:
            raise SearchMalformed(f"search returned HTTP {code}")
        try:
            return SearchResponse.model_validate(resp.json())
        except (ValueError, ValidationError) as err:
            raise SearchMalformed("search returned an unreadable response") from err
