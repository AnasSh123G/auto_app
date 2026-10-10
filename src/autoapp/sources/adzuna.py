"""Adzuna adapter (Germany). Needs ``ADZUNA_APP_ID`` / ``ADZUNA_APP_KEY``; skipped otherwise."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import date
from typing import Any

import httpx

from autoapp.config.models import Location, SearchProfile
from autoapp.sources.budget import RequestBudget
from autoapp.sources.models import RawJob

BASE_URL = "https://api.adzuna.com/v1/api/jobs/de/search"
USER_AGENT = "autoapp/0.1 (personal job search tool)"
PAGE_SIZE = 50
MIN_INTERVAL_S = 2.6  # about 23 requests a minute, under the free tier's reported 25
SKIP_MESSAGE = "adzuna: skipped (not configured)"


def _as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _is_predicted(value: Any) -> bool:
    """Adzuna sends ``salary_is_predicted`` as "0"/"1" (sometimes int or bool). Unknown counts as predicted."""
    return str(value).strip().lower() not in ("0", "false")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class AdzunaSource:
    """``JobSource`` for Adzuna's German job search API."""

    name = "adzuna"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        app_id: str | None = None,
        app_key: str | None = None,
        env: Mapping[str, str] | None = None,
        max_pages: int = 3,
        delay_s: float = MIN_INTERVAL_S,
        budget: RequestBudget | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        env = os.environ if env is None else env
        self._app_id = (app_id or env.get("ADZUNA_APP_ID") or "").strip()
        self._app_key = (app_key or env.get("ADZUNA_APP_KEY") or "").strip()
        self._client = client or httpx.Client(timeout=30, follow_redirects=True)
        self._max_pages = max_pages
        self._delay_s = delay_s
        self._sleep = sleep
        self._first_request = True
        self._budget = budget
        self.stop_reason: str | None = None  # why the search ended early (limit reached, rate limited)

    def skip_reason(self) -> str | None:
        """``"adzuna: skipped (not configured)"`` when credentials are missing, else ``None``."""
        return None if self._app_id and self._app_key else SKIP_MESSAGE

    def _params(self, profile: SearchProfile, keyword: str, location: Location | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "results_per_page": PAGE_SIZE,
            "what": keyword,
        }
        if profile.exclude_keywords:
            params["what_exclude"] = " ".join(profile.exclude_keywords)
        if location is not None:
            params["where"] = location.name
            if location.radius_km is not None:
                params["distance"] = location.radius_km
        if profile.max_posting_age_days is not None:
            params["max_days_old"] = profile.max_posting_age_days
        if profile.min_salary is not None:
            params["salary_min"] = profile.min_salary
        if profile.working_time == "full_time":
            params["full_time"] = 1
        elif profile.working_time == "part_time":
            params["part_time"] = 1
        if profile.contract_type == "permanent":
            params["permanent"] = 1
        elif profile.contract_type == "fixed_term":
            params["contract"] = 1
        return params

    def _get(self, page: int, params: dict[str, Any]) -> dict[str, Any] | None:
        """One page, or ``None`` once the budget is used up or Adzuna says "too many requests"."""
        if self.stop_reason:
            return None
        if self._budget is not None and not self._budget.take():
            self.stop_reason = self._budget.reason
            return None
        if not self._first_request and self._delay_s:
            self._sleep(self._delay_s)
        self._first_request = False
        resp = self._client.get(f"{BASE_URL}/{page}", params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code == 429:
            self.stop_reason = "Adzuna says too many requests (HTTP 429); stopped, jobs found so far are kept"
            return None
        resp.raise_for_status()
        return resp.json()

    def search(self, profile: SearchProfile) -> Iterator[RawJob]:
        """One request series per keyword x location; yields nothing when not configured.

        Stops early (``stop_reason``) when a request cap is reached or Adzuna returns 429.
        """
        if self.skip_reason():
            return
        seen: set[str] = set()
        for keyword in profile.keywords:
            for location in profile.locations or [None]:
                params = self._params(profile, keyword, location)
                for page in range(1, self._max_pages + 1):
                    data = self._get(page, params)
                    if data is None:
                        return
                    results = data.get("results") or []
                    for item in results:
                        job_id = str(item.get("id") or "")
                        if not job_id or job_id in seen:
                            continue
                        seen.add(job_id)
                        yield self._to_raw(item, job_id, profile.name)
                    total = _as_int(data.get("count"))
                    if len(results) < PAGE_SIZE or (total is not None and page * PAGE_SIZE >= total):
                        break

    @staticmethod
    def _to_raw(item: dict[str, Any], job_id: str, profile_name: str | None) -> RawJob:
        low, high = _as_int(item.get("salary_min")), _as_int(item.get("salary_max"))
        stated = not _is_predicted(item.get("salary_is_predicted"))
        location = (item.get("location") or {}).get("display_name")
        contract = item.get("contract_type")
        return RawJob(
            source="adzuna",
            source_job_id=job_id,
            title=item.get("title") or "",
            company=(item.get("company") or {}).get("display_name") or "",
            location=location,
            locations=[location] if location else [],
            url=item.get("redirect_url"),
            description=item.get("description"),  # truncated by Adzuna; the full text comes from detail fetch
            posted_at=_parse_date(item.get("created")),
            ad_salary_min=low if stated else None,
            ad_salary_max=high if stated else None,
            salary_estimate_min=None if stated else low,
            salary_estimate_max=None if stated else high,
            working_time=item.get("contract_time"),
            contract_type={"permanent": "permanent", "contract": "fixed_term"}.get(contract, contract),
            raw=item,
            search_profile=profile_name,
        )
