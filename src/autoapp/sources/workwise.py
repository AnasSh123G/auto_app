"""Workwise job search (https://www.workwise.io), without a login.

It sends the same search request Workwise's own website sends (``search.workwise.io/v2/searches``), once per
keyword of each search profile, a few pages each, with a pause between requests and request caps per run, day and
month. Workwise searches by keyword only; the distance to your profile's cities is checked here from the
coordinates Workwise gives for every job. The description is not part of the search result: the fetch step reads
it from the job page (``www.workwise.io/job/<id>-<slug>``), like for every other source.

Applications on Workwise go through Workwise itself (with a Workwise account): the job page is the application
entry, and the form finder and the assisted filling work from there.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx

from autoapp.config.models import SearchProfile
from autoapp.discovery.ingest import CITY_COORDS
from autoapp.discovery.normalize import norm_text
from autoapp.fetch.http_fetcher import USER_AGENT
from autoapp.sources.budget import RequestBudget
from autoapp.sources.models import RawJob

SEARCH_URL = "https://search.workwise.io/v2/searches"
JOB_URL = "https://www.workwise.io/job/{id}-{slug}"
# The search body the website sends, with only the keyword filled in.
_BODY: dict[str, Any] = {
    "description": "", "end": None, "hoursEnd": None, "hoursStart": None, "languageLevels": [],
    "leadershipExperience": "does_not_matter", "locationLevels": [], "mobileWork": None, "occupations": [],
    "ongoing": True, "remoteWork": "does_not_matter", "salary": None, "salaryFlexible": None, "salaryType": None,
    "searchCompanyLevels": [], "searchCompanyTagLevels": [], "searchesEnquiryTypes": [], "start": None,
    "workExperience": "does_not_matter", "worldwide": True,
}
_WORK_MODE = {"not_possible": "onsite", "possible": "hybrid", "partially": "hybrid", "mandatory": "remote",
              "only": "remote", "fully": "remote"}
_CITY_ALIASES = {"cologne": "koeln", "munich": "muenchen", "nuremberg": "nuernberg", "frankfurt": "frankfurt am main"}


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _coords(name: str) -> tuple[float, float] | None:
    key = norm_text(name.split(",")[0])
    return CITY_COORDS.get(_CITY_ALIASES.get(key, key))


def _as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _working_time(start: Any, end: Any) -> str | None:
    lo, hi = _as_int(start), _as_int(end)
    if lo is not None and lo >= 35:
        return "full_time"
    if hi is not None and hi <= 30:
        return "part_time"
    return None


class WorkwiseSource:
    """``JobSource`` for Workwise's public job search."""

    name = "workwise"

    def __init__(self, client: httpx.Client | None = None, *, max_pages: int = 2, page_size: int = 50,
                 delay_s: float = 2, budget: RequestBudget | None = None,
                 sleep: Callable[[float], None] = time.sleep, today: date | None = None) -> None:
        self._client = client
        self._max_pages, self._size, self._delay = max_pages, page_size, delay_s
        self._budget, self._sleep = budget, sleep
        self._today = today or datetime.now(UTC).date()
        self._requests = 0
        self._seen: set[str] = set()
        self.stop_reason: str | None = None
        self.diagnostics: Counter[str] = Counter()

    def skip_reason(self) -> None:
        return None

    def search(self, profile: SearchProfile) -> Iterator[RawJob]:
        client = self._client or httpx.Client(timeout=30, follow_redirects=False)
        try:
            for keyword in profile.keywords:
                for page in range(1, self._max_pages + 1):
                    if self.stop_reason:
                        return
                    items, pages = self._page(client, keyword, page)
                    if items is None:
                        return
                    for item in items:
                        job = self._job(item, profile)
                        if job is not None:
                            yield job
                    if page >= pages:
                        break
        finally:
            if self._client is None:
                client.close()

    def _page(self, client: httpx.Client, keyword: str, page: int) -> tuple[list[dict] | None, int]:
        if self._budget is not None and not self._budget.take():
            self.stop_reason = f"partial: {self._budget.reason}"
            return None, 0
        if self._requests:
            self._sleep(self._delay)
        self._requests += 1
        self.diagnostics["requests"] += 1
        try:
            resp = client.post(SEARCH_URL, params={"size": self._size, "page": page, "withMatchings": "true"},
                               json={**_BODY, "description": keyword},
                               headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                        "Accept-Language": "de", "Referer": "https://www.workwise.io/"})
        except httpx.HTTPError as exc:
            self.stop_reason = f"partial/error: {type(exc).__name__}; stopped without retry"
            return None, 0
        if resp.status_code != 200:
            self.stop_reason = f"partial/error: HTTP {resp.status_code}; stopped without retry"
            return None, 0
        try:
            result = resp.json()["data"]["matchingResult"]
            items = [m["enquiries"] for m in result["data"]["matchings"] if isinstance(m, dict) and "enquiries" in m]
            pages = int(result.get("paging", {}).get("pages") or 1)
        except (ValueError, KeyError, TypeError):
            self.stop_reason = "partial/error: unexpected answer format"
            return None, 0
        return items, pages

    def _job(self, item: dict, profile: SearchProfile) -> RawJob | None:
        try:
            job_id, title, slug = str(item["id"]), str(item["name"]).strip(), str(item.get("slug") or "job")
        except (KeyError, TypeError):
            self.diagnostics["invalid"] += 1
            return None
        if not title or item.get("status", "open") != "open":
            self.diagnostics["invalid"] += 1
            return None
        company_info = item.get("company") or {}
        anonymous = bool(company_info.get("displayAnonymously"))
        company = "" if anonymous else str(company_info.get("name") or "").strip()
        places = [loc for loc in item.get("locationLevels") or [] if isinstance(loc, dict)]
        cities = [str(loc["city"]) for loc in places if loc.get("city")]
        work_mode = _WORK_MODE.get(str(item.get("remoteWork") or ""))
        if not self._near(places, work_mode, profile):
            self.diagnostics["outside your area"] += 1
            return None
        if job_id in self._seen:  # found again by another keyword
            return None
        self._seen.add(job_id)
        kind = item.get("type") or {}
        salary_per_year = kind.get("salaryType") == "per year"
        lo, hi = (_as_int(item.get("minSalary")), _as_int(item.get("maxSalary"))) if salary_per_year else (None, None)
        return RawJob(
            source=self.name, source_job_id=job_id, title=title,
            company=company or "(company not named on Workwise)",
            location=cities[0] if cities else ("Remote" if work_mode == "remote" else None), locations=cities,
            url=JOB_URL.format(id=job_id, slug=slug), work_mode=work_mode,
            working_time=_working_time(item.get("hoursStart"), item.get("hoursEnd")),
            contract_type="permanent" if kind.get("shortName") == "employment" else None,
            # Salary ranges on Workwise are set by the employer for the job (not a platform estimate).
            ad_salary_min=lo if lo else None, ad_salary_max=hi if hi else None,
            search_profile=profile.name,
            raw={"company_unknown": not company, "type": kind.get("name"), "remote_work": item.get("remoteWork"),
                 "short_description": item.get("shortDescription")},
        )

    def _near(self, places: list[dict], work_mode: str | None, profile: SearchProfile) -> bool:
        """Within the radius of one of the profile's cities (a remote job is always near)."""
        if not profile.locations or work_mode == "remote":
            return True
        points = [(float(p["lat"]), float(p["lon"])) for p in places
                  if isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float))]
        if not points:
            return True  # no coordinates: kept, the evaluation looks at the location
        for wanted in profile.locations:
            home = _coords(wanted.name)
            if home is None:
                return True  # a city the app has no coordinates for: cannot judge, so kept
            radius = wanted.radius_km if wanted.radius_km is not None else 30
            if any(_km(home, point) <= radius for point in points):
                return True
        return False
