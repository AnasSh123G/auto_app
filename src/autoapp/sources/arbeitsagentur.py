"""Arbeitsagentur Jobsuche adapter (public bundesAPI endpoints).

Search: ``GET /pc/v6/jobs``; detail: ``GET /pc/v4/jobdetails/{base64(refnr)}``.
Only the filters a profile actually configures are sent.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import httpx

from autoapp.config.models import Location, SearchProfile
from autoapp.sources.models import RawJob

BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
SEARCH_PATH = "/pc/v6/jobs"
DETAIL_PATH = "/pc/v4/jobdetails/"
API_KEY = "jobboerse-jobsuche"
USER_AGENT = "autoapp/0.1 (personal job search tool)"
JOB_PAGE_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"
PAGE_SIZE = 50
MAX_AGE_DAYS = 100  # the API accepts 0..100 for veroeffentlichtseit

_CONTACT_KEY_PARTS = ("kontakt", "bewerbung", "email", "e-mail", "telefon", "ansprech")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _cities(item: dict[str, Any]) -> list[str]:
    """City names from a v6/v4 ``stellenlokationen`` list (or the older ``arbeitsort``/``arbeitsorte``)."""
    out: list[str] = []
    for loc in item.get("stellenlokationen") or []:
        ort = (loc.get("adresse") or {}).get("ort")
        if ort and ort not in out:
            out.append(ort)
    for key in ("arbeitsorte", "arbeitsort"):
        value = item.get(key)
        for loc in value if isinstance(value, list) else [value] if isinstance(value, dict) else []:
            ort = loc.get("ort")
            if ort and ort not in out:
                out.append(ort)
    return out


def _working_time(item: dict[str, Any]) -> str | None:
    full = bool(item.get("arbeitszeitVollzeit"))
    part = any(item.get(k) for k in item if k.startswith("arbeitszeitTeilzeit"))
    if full and not part:
        return "full_time"
    if part and not full:
        return "part_time"
    return None


def _contract_type(item: dict[str, Any]) -> str | None:
    term = str(item.get("vertragsdauer") or item.get("befristung") or "").upper()
    if term == "UNBEFRISTET":
        return "permanent"
    if term == "BEFRISTET":
        return "fixed_term"
    return None


def _work_mode(item: dict[str, Any]) -> str | None:
    if not item.get("homeofficemoeglich"):
        return None
    kind = str(item.get("homeofficetyp") or "").upper()
    return "remote" if "VOLL" in kind or "KOMPLETT" in kind else "hybrid"


def _ad_salary(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """Employer-stated annual EUR range from ``gehaltsspanneVon/Bis`` (monthly pay is x12)."""
    low, high = _as_int(item.get("gehaltsspanneVon")), _as_int(item.get("gehaltsspanneBis"))
    kind = str(item.get("verguetungsangabe") or "").upper()
    if kind == "MONATSGEHALT":
        return (low * 12 if low else None), (high * 12 if high else None)
    if kind in ("JAHRESGEHALT", ""):
        return low, high
    return None, None  # hourly or unknown unit: not comparable


def _contact_fields(item: dict[str, Any]) -> str | None:
    found: list[str] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(value, list):
            for v in value:
                walk(prefix, v)
        elif value not in (None, "", False) and any(p in prefix.lower() for p in _CONTACT_KEY_PARTS):
            found.append(f"{prefix}: {value}")

    walk("", item)
    return "\n".join(found) or None


def _search_params(profile: SearchProfile, keyword: str, location: Location | None, page: int) -> dict[str, Any]:
    """Build query params; only configured filters are included."""
    params: dict[str, Any] = {"was": keyword, "angebotsart": 1, "size": PAGE_SIZE, "page": page}
    if location is not None:
        params["wo"] = location.name
        if location.radius_km is not None:
            params["umkreis"] = location.radius_km
    if profile.max_posting_age_days is not None:
        params["veroeffentlichtseit"] = min(profile.max_posting_age_days, MAX_AGE_DAYS)
    arbeitszeit: list[str] = []
    if profile.working_time == "full_time":
        arbeitszeit.append("vz")
    elif profile.working_time == "part_time":
        arbeitszeit.append("tz")
    if profile.work_mode in ("remote", "hybrid"):
        arbeitszeit.append("ho")
    if arbeitszeit:
        params["arbeitszeit"] = ";".join(arbeitszeit)
    if profile.contract_type == "permanent":
        params["befristung"] = 2
    elif profile.contract_type == "fixed_term":
        params["befristung"] = 1
    if profile.exclude_agencies:
        params["zeitarbeit"] = "false"
        params["pav"] = "false"
    return params


class ArbeitsagenturSource:
    """``JobSource`` for the Bundesagentur für Arbeit job board."""

    name = "arbeitsagentur"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        max_pages: int = 3,
        delay_s: float = 0.3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=30, follow_redirects=True)
        self._max_pages = max_pages
        self._delay_s = delay_s
        self._sleep = sleep
        self._first_request = True

    def skip_reason(self) -> str | None:
        """Always usable: the API key is a public client id."""
        return None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._first_request and self._delay_s:
            self._sleep(self._delay_s)
        self._first_request = False
        resp = self._client.get(
            BASE_URL + path,
            params=params,
            headers={"X-API-Key": API_KEY, "User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return resp.json()

    def _pages(self, profile: SearchProfile, keyword: str, location: Location | None) -> Iterator[dict[str, Any]]:
        for page in range(1, self._max_pages + 1):
            data = self._get(SEARCH_PATH, _search_params(profile, keyword, location, page))
            items = data.get("ergebnisliste") or data.get("stellenangebote") or []
            yield from items
            total = _as_int(data.get("maxErgebnisse"))
            if len(items) < PAGE_SIZE or (total is not None and page * PAGE_SIZE >= total):
                return

    def search(self, profile: SearchProfile) -> Iterator[RawJob]:
        """One request series per keyword x location (no location: all Germany); merged by refnr."""
        seen: set[str] = set()
        for keyword in profile.keywords:
            for location in profile.locations or [None]:
                for item in self._pages(profile, keyword, location):
                    refnr = item.get("referenznummer") or item.get("refnr")
                    if not refnr or refnr in seen:
                        continue
                    seen.add(refnr)
                    yield self._to_raw(item, refnr, profile.name)

    @staticmethod
    def _to_raw(item: dict[str, Any], refnr: str, profile_name: str | None) -> RawJob:
        cities = _cities(item)
        published = item.get("veroeffentlichungszeitraum") or {}
        posted = (
            published.get("von") if isinstance(published, dict) else None
        ) or item.get("aktuelleVeroeffentlichungsdatum") or item.get("datumErsteVeroeffentlichung")
        external = item.get("externeURL") or item.get("externeUrl")
        low, high = _ad_salary(item)
        return RawJob(
            source="arbeitsagentur",
            source_job_id=refnr,
            title=item.get("stellenangebotsTitel") or item.get("titel") or item.get("beruf") or "",
            company=item.get("firma") or item.get("arbeitgeber") or "",
            location=cities[0] if cities else None,
            locations=cities,
            url=JOB_PAGE_URL + refnr,
            application_url=external,
            posted_at=_parse_date(posted),
            ad_salary_min=low,
            ad_salary_max=high,
            work_mode=_work_mode(item),
            working_time=_working_time(item),
            contract_type=_contract_type(item),
            raw=item,
            search_profile=profile_name,
        )

    def fetch_detail(self, raw: RawJob) -> RawJob:
        """Fetch ``jobdetails`` for ``raw.source_job_id`` and return an enriched copy."""
        if not raw.source_job_id:
            return raw
        encoded = base64.b64encode(raw.source_job_id.encode()).decode()
        detail = self._get(DETAIL_PATH + encoded)
        cities = _cities(detail)
        low, high = _ad_salary(detail)
        update: dict[str, Any] = {
            "description": detail.get("stellenangebotsBeschreibung") or raw.description,
            "title": detail.get("stellenangebotsTitel") or detail.get("titel") or raw.title,
            "company": detail.get("firma") or detail.get("arbeitgeber") or raw.company,
            "application_url": detail.get("externeURL") or detail.get("externeUrl") or raw.application_url,
            "contact_fields": _contact_fields(detail) or raw.contact_fields,
            "work_mode": _work_mode(detail) or raw.work_mode,
            "working_time": _working_time(detail) or raw.working_time,
            "contract_type": _contract_type(detail) or raw.contract_type,
            "ad_salary_min": low if low is not None else raw.ad_salary_min,
            "ad_salary_max": high if high is not None else raw.ad_salary_max,
            "raw": {**raw.raw, "detail": {k: v for k, v in detail.items() if k != "stellenangebotsBeschreibung"}},
        }
        if cities:
            update["location"] = cities[0]
            update["locations"] = cities
        published = detail.get("veroeffentlichungszeitraum") or {}
        posted = _parse_date(published.get("von") if isinstance(published, dict) else None)
        if posted:
            update["posted_at"] = posted
        return raw.model_copy(update=update)
