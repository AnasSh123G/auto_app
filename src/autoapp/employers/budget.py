"""Page accounting for one employer: verification and scanning share the per-employer and per-run limits."""

from __future__ import annotations

from collections.abc import Callable

from autoapp.employers.crawl import RunBudget
from autoapp.fetch.http_fetcher import FetchResult


class PageMeter:
    def __init__(self, run: RunBudget, employer_limit: int) -> None:
        self.run, self.limit, self.used = run, employer_limit, 0

    @property
    def left(self) -> int:
        return max(0, min(self.limit - self.used, self.run.pages))

    def wrap(self, fetch: Callable[[str], FetchResult]) -> Callable[[str], FetchResult]:
        def counted(url: str) -> FetchResult:
            if self.used >= self.limit or not self.run.take_page():
                return FetchResult(url=url, status=0, rejected=True, reason="page limit reached")
            self.used += 1
            return fetch(url)
        return counted
