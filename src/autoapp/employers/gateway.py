"""Budgeted, run-scoped access to the search service (caps, the two-per-employer ceiling, run-level stops)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from autoapp.config.models import EmployerCareersConfig
from autoapp.employers.parallel import (
    SOURCE,
    ParallelSearch,
    SearchAuthError,
    SearchError,
    SearchNoKey,
    SearchRateLimited,
    SearchResponse,
)
from autoapp.sources.budget import RequestBudget

PER_EMPLOYER_LIMIT = 2


@dataclass
class SearchUsage:
    requests: int = 0
    failures: int = 0
    estimated_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)


class SearchUnavailableForRun(Exception):
    """No further search request will be sent in this run; ``reason`` says why."""

    def __init__(self, reason: str, kind: str) -> None:
        super().__init__(reason)
        self.reason, self.kind = reason, kind


class SearchGateway:
    def __init__(self, conn: sqlite3.Connection, config: EmployerCareersConfig, client: ParallelSearch,
                 reserve: int = 0) -> None:
        self._client = client
        self._config = config
        self._budget = RequestBudget(
            conn, SOURCE, per_run=config.max_search_requests_per_run, per_day=config.max_search_requests_per_day,
            per_month=config.max_search_requests_per_month,
            reserve=min(reserve, config.max_search_requests_per_run // 2))
        self._per_employer: dict[str, int] = {}
        self._stopped: tuple[str, str] | None = None
        if not client.has_key:
            self._stopped = ("PARALLEL_API_KEY is not set; employers without a saved site are skipped", "no_key")
        self.usage = SearchUsage()

    @property
    def stop_reason(self) -> str | None:
        return self._stopped[0] if self._stopped else (self._budget.reason)

    @property
    def stop_kind(self) -> str | None:
        if self._stopped:
            return self._stopped[1]
        return "cap" if self._budget.reason else None

    def remaining_for(self, employer_key: str) -> int:
        return PER_EMPLOYER_LIMIT - self._per_employer.get(employer_key, 0)

    def search(self, employer_key: str, objective: str, queries: list[str]) -> SearchResponse:
        """One attempted search. The reservation is kept even when the call fails; nothing is retried."""
        if self._stopped:
            raise SearchUnavailableForRun(*self._stopped)
        if self.remaining_for(employer_key) <= 0:
            raise SearchUnavailableForRun("two searches already used for this employer", "employer_limit")
        if not self._budget.take():
            raise SearchUnavailableForRun(self._budget.reason or "search cap reached", "cap")
        self._per_employer[employer_key] = self._per_employer.get(employer_key, 0) + 1
        self.usage.requests += 1
        self.usage.estimated_usd = self.usage.requests * self._config.search_cost_per_1000_usd / 1000
        try:
            response = self._client.search(objective, queries)
        except SearchNoKey as err:
            self._stopped = (str(err), err.kind)
            self.usage.failures += 1
            raise
        except SearchAuthError as err:
            self._stopped = (str(err), err.kind)
            self.usage.failures += 1
            raise
        except SearchRateLimited as err:
            self._stopped = (str(err), err.kind)
            self.usage.failures += 1
            raise
        except SearchError:
            self.usage.failures += 1
            raise
        self.usage.warnings += response.warning_texts
        return response
