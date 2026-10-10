"""Bounded public Arbeitnow feed, shared across locally matched search profiles."""

from __future__ import annotations

import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from autoapp.config.models import SearchProfile
from autoapp.discovery.ingest import CITY_COORDS, distance_km
from autoapp.discovery.normalize import norm_text
from autoapp.discovery.prefilter import Candidate, check
from autoapp.fetch.extract import html_to_text
from autoapp.fetch.http_fetcher import USER_AGENT
from autoapp.sources.budget import RequestBudget
from autoapp.sources.models import RawJob

BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
_GERMAN_HOSTS = frozenset({"arbeitnow.com", "www.arbeitnow.com"})
_FOREIGN_HOSTS = frozenset({
    "arbeitnow.co.uk", "www.arbeitnow.co.uk", "arbeitnow.fr", "www.arbeitnow.fr",
    "arbeitnow.ch", "www.arbeitnow.ch",
})
_FOREIGN_COUNTRIES = re.compile(
    r"\b(?:united kingdom|uk|england|france|frankreich|switzerland|schweiz|austria|oesterreich|"
    r"netherlands|niederlande|belgium|belgien|poland|polen|spain|spanien|italy|italien|"
    r"portugal|denmark|daenemark|sweden|schweden|norway|norwegen|finland|finnland|ireland|irland|"
    r"united states|usa|canada|kanada|czech republic|tschechien|australia|australien|"
    r"india|indien|china|japan|singapore|singapur|new zealand|neuseeland|brazil|brasilien)\b"
)
_CITY_ALIASES = {
    "cologne": "koeln", "munich": "muenchen", "dusseldorf": "duesseldorf",
    "nuremberg": "nuernberg", "hanover": "hannover", "frankfurt am main": "frankfurt",
}


def listing_url(value: str | None) -> str | None:
    """A safe canonical backlink, never an employer or application URL."""
    if not value or re.search(r"[\s\x00-\x1f\x7f]", value):
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or p.hostname not in _GERMAN_HOSTS:
            return None
        if p.username is not None or p.password is not None or p.port not in {None, 443 if p.scheme == "https" else 80}:
            return None
        path = unquote(p.path)
        if not path.startswith("/jobs/") or not path[6:] or any(x in {".", ".."} for x in path.split("/")):
            return None
        if re.search(r"[\x00-\x1f\x7f\\]", path):
            return None
        return "https://www.arbeitnow.com" + quote(path, safe="/-._~")
    except ValueError:
        return None


def source_link(sources: Iterable[sqlite3.Row | Mapping[str, object]]) -> str | None:
    for row in sources:
        if row["source"] == "arbeitnow":
            url = row["url"]
            safe = listing_url(url if isinstance(url, str) else None)
            if safe:
                return safe
    return None


class FeedRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    slug: str
    title: str
    company_name: str
    url: str
    description: str | None = None
    location: str | None = None
    country: str | None = None
    remote: StrictBool | None = None
    tags: list[str] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    created_at: StrictInt | None = None


def _city(text: str | None) -> str:
    name = norm_text((text or "").split(",")[0])
    return _CITY_ALIASES.get(name, name)


def _location_match(location: str | None, profile: SearchProfile) -> tuple[bool, bool]:
    actual = _city(location)
    if not profile.locations:
        return True, actual not in CITY_COORDS
    unknown = False
    for wanted in profile.locations:
        city = _city(wanted.name)
        if actual and actual == city:
            return True, False
        distance = distance_km(actual, city)
        if distance is None:
            unknown = True
        elif wanted.radius_km is not None and distance <= wanted.radius_km:
            return True, False
    return unknown, unknown


def _job_type(types: list[str], mapping: Mapping[str, str]) -> str | None:
    kinds = {mapping[norm_text(t)] for t in types if norm_text(t) in mapping}
    return next(iter(kinds)) if len(kinds) == 1 else None


def candidate_of(raw: RawJob) -> Candidate:
    candidate = Candidate.from_raw_job(raw)
    types = raw.raw.get("job_types", [])
    if isinstance(types, list) and all(isinstance(t, str) for t in types):
        candidate.contract_type = " ".join([raw.contract_type or "", *types]).strip() or None
    return candidate


class ArbeitnowSource:
    name = "arbeitnow"

    def __init__(
        self, client: httpx.Client | None = None, *, profiles: list[SearchProfile] | None = None,
        max_pages: int = 3, delay_s: float = 3, budget: RequestBudget | None = None,
        sleep: Callable[[float], None] = time.sleep, today: date | None = None,
    ) -> None:
        self._client, self._profiles = client, profiles
        self._max_pages, self._delay, self._budget, self._sleep = max_pages, delay_s, budget, sleep
        self._today = today or datetime.now(UTC).date()
        self._loaded = False
        self._jobs: list[RawJob] = []
        self.stop_reason: str | None = None
        self.diagnostics: Counter[str] = Counter()
        self.warnings: Counter[str] = Counter()

    def skip_reason(self) -> None:
        return None

    @property
    def report_note(self) -> str | None:
        notes = [self.stop_reason] if self.stop_reason else []
        notes += [f"{reason}: {count}" for reason, count in sorted(self.warnings.items())]
        return "; ".join(notes) or None

    def search(self, profile: SearchProfile) -> Iterator[RawJob]:
        if not self._loaded:
            self._loaded = True
            self._load(self._profiles if self._profiles is not None else [profile])
        yield from (job for job in self._jobs if job.search_profile == profile.name)

    def _load(self, profiles: list[SearchProfile]) -> None:
        client = self._client or httpx.Client(timeout=30, follow_redirects=False)
        seen: set[str] = set()
        try:
            for page in range(1, self._max_pages + 1):
                if self._budget is not None and not self._budget.take():
                    self.stop_reason = f"partial: {self._budget.reason}"
                    break
                if page > 1:
                    self._sleep(self._delay)
                self.diagnostics["requests"] += 1
                try:
                    resp = client.get(BASE_URL, params={"page": page}, follow_redirects=False,
                                      headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
                    self.diagnostics["pages"] += 1
                    if resp.status_code != 200:
                        self.stop_reason = f"partial/error: HTTP {resp.status_code}; stopped without retry"
                        break
                    data = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    self.stop_reason = f"partial/error: {type(exc).__name__}; stopped without retry"
                    break
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    self.stop_reason = "partial/error: invalid data array"
                    break
                for item in data["data"]:
                    self._record(item, profiles, seen)
                links = data.get("links")
                if not isinstance(links, dict) or "next" not in links:
                    self.stop_reason = "partial/error: invalid pagination envelope"
                    break
                nxt = links["next"]
                if nxt is None:
                    break
                if not self._valid_next(nxt, page):
                    self.stop_reason = "partial/error: unsafe or nonsequential pagination"
                    break
                if page == self._max_pages:
                    self.stop_reason = f"partial: page limit reached ({self._max_pages})"
        finally:
            if self._client is None:
                client.close()

    @staticmethod
    def _valid_next(value: object, page: int) -> bool:
        if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f]", value):
            return False
        try:
            p = urlsplit(value)
            return (p.scheme == "https" and p.hostname == "www.arbeitnow.com"
                    and p.path == "/api/job-board-api" and p.port in {None, 443}
                    and p.username is None and p.password is None and not p.fragment
                    and parse_qsl(p.query, keep_blank_values=True) == [("page", str(page + 1))])
        except ValueError:
            return False

    def _record(self, item: object, profiles: list[SearchProfile], seen: set[str]) -> None:
        if not isinstance(item, dict):
            self.diagnostics["invalid"] += 1
            self.warnings["invalid record"] += 1
            return
        item = dict(item)
        # Bad optional facts must not turn into guessed values or discard a valid vacancy.
        for key, valid in (
            ("created_at", lambda v: type(v) is int),
            ("remote", lambda v: type(v) is bool),
            ("tags", lambda v: isinstance(v, list) and all(isinstance(t, str) for t in v)),
            ("job_types", lambda v: isinstance(v, list) and all(isinstance(t, str) for t in v)),
            ("description", lambda v: isinstance(v, str)),
            ("location", lambda v: isinstance(v, str)),
            ("country", lambda v: isinstance(v, str)),
        ):
            if item.get(key) is not None and not valid(item[key]):
                self.warnings[f"invalid optional {key}"] += 1
                item[key] = [] if key in {"tags", "job_types"} else None
            elif item.get(key) is None and key in {"tags", "job_types"}:
                item[key] = []
        try:
            record = FeedRecord.model_validate(item)
        except ValidationError:
            self.diagnostics["invalid"] += 1
            self.warnings["invalid required fields"] += 1
            return
        if not all(s.strip() for s in (record.slug, record.title, record.company_name)):
            self.diagnostics["invalid"] += 1
            self.warnings["empty required fields"] += 1
            return
        try:
            foreign = urlsplit(record.url).hostname in _FOREIGN_HOSTS
        except ValueError:
            foreign = False
        loc = norm_text(record.location)
        country = norm_text(record.country)
        country_conflict = bool(
            country and country not in {"de", "deu", "unknown", "unspecified", "remote", "worldwide",
                                        "anywhere", "europe", "eu", "emea"}
            and not re.search(r"\b(?:germany|deutschland)\b", country)
        )
        if foreign or country_conflict or (
                _FOREIGN_COUNTRIES.search(loc) and not re.search(r"\b(?:germany|deutschland)\b", loc)):
            self.diagnostics["foreign"] += 1
            return
        url = listing_url(record.url)
        if url is None:
            self.diagnostics["invalid"] += 1
            self.warnings["invalid listing URL"] += 1
            return
        if record.slug in seen:
            return
        seen.add(record.slug)
        posted = None
        if record.created_at is not None:
            try:
                if record.created_at <= 0:
                    raise ValueError("invalid timestamp")
                posted = datetime.fromtimestamp(record.created_at, UTC).date()
                if posted > self._today:
                    raise ValueError("future timestamp")
            except (ValueError, OverflowError, OSError):
                posted = None
                self.warnings["invalid optional created_at"] += 1
        work_mode = "remote" if record.remote is True else _job_type(
            record.job_types, {"onsite": "onsite", "on site": "onsite", "hybrid": "hybrid"})
        raw = RawJob(
            source=self.name, source_job_id=record.slug, title=record.title.strip(),
            company=record.company_name.strip(), url=url, location=record.location,
            description=html_to_text(record.description) if record.description else None,
            posted_at=posted, work_mode=work_mode,
            working_time=_job_type(record.job_types, {
                "full time": "full_time", "vollzeit": "full_time", "part time": "part_time", "teilzeit": "part_time"}),
            contract_type=_job_type(record.job_types, {
                "permanent": "permanent", "unbefristet": "permanent", "unbefristeter arbeitsvertrag": "permanent",
                "fixed term": "fixed_term", "befristet": "fixed_term", "freelance": "freelance",
            }),
            raw={"tags": record.tags, "job_types": record.job_types, "remote": record.remote,
                 "country": record.country,
                 "source_created_at": record.created_at, "date_basis": "source listing, not employer publication"},
        )
        haystacks = [f" {norm_text(s)} " for s in [raw.title, *record.tags, raw.description or ""]]
        relevant: list[SearchProfile] = []
        uncertain = False
        for profile in profiles:
            if not any(f" {norm_text(k)} " in text for k in profile.keywords for text in haystacks):
                continue
            fits, unknown = _location_match(raw.location, profile)
            if fits:
                relevant.append(profile)
                uncertain |= unknown
        if not relevant:
            return
        accepted = [p for p in relevant if check(candidate_of(raw), p, today=self._today) is None]
        raw.search_profile = (accepted or relevant)[0].name
        raw.raw.update({"profiles": [p.name for p in relevant], "accepting_profiles": [p.name for p in accepted],
                        "location_uncertain": uncertain, "work_eligibility": "unknown"})
        if uncertain:
            self.diagnostics["uncertain"] += 1
            self.warnings["location unresolved; eligibility needs review"] += 1
        self._jobs.append(raw)
