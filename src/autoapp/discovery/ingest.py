"""Turn source results into job records with deduplication (design decision 6).

Matching order: source job ID → canonical URL → fingerprint (company + title + city)
→ multi-city group (company + title within 30 days). A match links the posting to the
existing job instead of creating a new one, so every role is evaluated at most once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from autoapp.db.repo import Repo
from autoapp.discovery.normalize import canonical_url, fingerprint, group_key, norm_text
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.models import RawJob
from autoapp.sources.stepstone_links import ResolvedLink

GROUP_WINDOW_DAYS = 30
EMPLOYER_SOURCE = "employer-careers"

# Approximate coordinates (lat, lon) of larger German cities, used only to pick the
# primary city of a multi-city posting. Unknown cities simply never win.
CITY_COORDS: dict[str, tuple[float, float]] = {
    "berlin": (52.52, 13.405), "hamburg": (53.551, 9.994), "muenchen": (48.137, 11.575),
    "koeln": (50.938, 6.96), "frankfurt am main": (50.11, 8.682), "frankfurt": (50.11, 8.682),
    "stuttgart": (48.776, 9.183), "duesseldorf": (51.227, 6.773), "leipzig": (51.34, 12.375),
    "dortmund": (51.514, 7.468), "essen": (51.456, 7.012), "bremen": (53.079, 8.802),
    "dresden": (51.05, 13.738), "hannover": (52.375, 9.732), "nuernberg": (49.452, 11.077),
    "duisburg": (51.435, 6.763), "bochum": (51.482, 7.216), "wuppertal": (51.256, 7.151),
    "bielefeld": (52.03, 8.532), "bonn": (50.737, 7.098), "muenster": (51.961, 7.626),
    "mannheim": (49.487, 8.466), "karlsruhe": (49.007, 8.404), "augsburg": (48.371, 10.898),
    "wiesbaden": (50.078, 8.24), "mainz": (49.993, 8.247), "kiel": (54.323, 10.123),
    "aachen": (50.776, 6.084), "freiburg": (47.999, 7.842), "freiburg im breisgau": (47.999, 7.842),
    "heidelberg": (49.399, 8.672), "darmstadt": (49.873, 8.651), "regensburg": (49.013, 12.102),
    "ulm": (48.401, 9.988), "potsdam": (52.391, 13.064), "rostock": (54.092, 12.099),
    "erfurt": (50.979, 11.033), "magdeburg": (52.131, 11.64), "saarbruecken": (49.234, 6.997),
    "kassel": (51.312, 9.48), "braunschweig": (52.269, 10.521), "osnabrueck": (52.279, 8.047),
    "oldenburg": (53.144, 8.214), "ruesselsheim": (49.995, 8.412), "ruesselsheim am main": (49.995, 8.412),
    "offenburg": (48.473, 7.944), "pforzheim": (48.892, 8.694), "neuss": (51.2, 6.692),
    "ingolstadt": (48.766, 11.426), "wolfsburg": (52.423, 10.787), "jena": (50.927, 11.589),
    "luebeck": (53.866, 10.686), "chemnitz": (50.833, 12.92), "halle": (51.483, 11.97),
    "st. ingbert": (49.277, 7.116), "sankt ingbert": (49.277, 7.116),
}


def _coords(city: str | None) -> tuple[float, float] | None:
    if not city:
        return None
    key = norm_text(city.split(",")[0])
    return CITY_COORDS.get(key)


def distance_km(a: str | None, b: str | None) -> float | None:
    ca, cb = _coords(a), _coords(b)
    if not ca or not cb:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*ca, *cb))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class IngestOutcome:
    job_id: str
    created: bool
    matched_by: Literal["source", "url", "fingerprint", "group"] | None = None


def employer_ref(source: str, source_job_id: str | None) -> str | None:
    """The employer-scoped requisition key of an ``employer-careers`` source id (``emp:<employer>:<key>``)."""
    if source == EMPLOYER_SOURCE and source_job_id and source_job_id.startswith("emp:"):
        return source_job_id
    return None


def job_requisitions(repo: Repo, job_id: str) -> set[str]:
    """Employer-scoped requisition keys attached to a job."""
    return {ref for src in repo.sources(job_id) if (ref := employer_ref(src["source"], src["source_job_id"]))}


def _unambiguous(repo: Repo, matches: list) -> object | None:
    """The one job a role-based match may link to. Several jobs holding different verified requisitions
    mean the role is not one vacancy, so nothing is linked and the posting stays separate."""
    if not matches:
        return None
    if len({ref for m in matches for ref in job_requisitions(repo, m["id"])}) > 1:
        return None
    return matches[0]


def _city(raw: RawJob) -> str | None:
    return raw.location or (raw.locations[0] if raw.locations else None)


def _job_fields(raw: RawJob) -> dict:
    city = _city(raw)
    return {
        "title": raw.title,
        "company": raw.company,
        "location": city,
        "work_mode": raw.work_mode,
        "working_time": raw.working_time,
        "contract_type": raw.contract_type,
        "job_url": raw.url,
        "canonical_url": canonical_url(raw.url) if raw.url else None,
        "application_url": raw.application_url,
        "description": raw.description,
        "posted_at": raw.posted_at.isoformat() if raw.posted_at else None,
        "ad_salary_min": raw.ad_salary_min,
        "ad_salary_max": raw.ad_salary_max,
        "salary_estimate_min": raw.salary_estimate_min,
        "salary_estimate_max": raw.salary_estimate_max,
        "fingerprint": fingerprint(raw.company, raw.title, city),
        "group_key": group_key(raw.company, raw.title),
        "search_profile": raw.search_profile,
    }


def _source_raw(raw: RawJob) -> dict:
    data = dict(raw.raw)
    if raw.contact_fields:
        data["contact_fields"] = raw.contact_fields
    return data


def _fill_gaps(repo: Repo, job_id: str, raw: RawJob) -> None:
    """A later source may know more (e.g. the full description); fill empty fields only."""
    job = repo.get_job(job_id)
    if job is None:
        return
    updates = {}
    for key, value in _job_fields(raw).items():
        if key in ("fingerprint", "group_key", "location", "title", "company", "canonical_url", "job_url"):
            continue
        if value is not None and job[key] in (None, ""):
            updates[key] = value
    if updates:
        repo.update_job(job_id, **updates)


def ingest(repo: Repo, raw: RawJob, *, home_city: str | None = None, now: datetime | None = None) -> IngestOutcome:
    """Store ``raw`` as a new job, or link it to the job it duplicates.

    ``home_city`` is the search profile's location; for multi-city postings the city
    closest to it becomes the job's primary location.
    """
    url = canonical_url(raw.url) if raw.url else None
    city = _city(raw)

    employer_posting = raw.source == EMPLOYER_SOURCE
    existing = repo.find_by_source(raw.source, raw.source_job_id) if raw.source_job_id else None
    if existing is not None:
        if employer_posting and city and norm_text(city) != norm_text(existing["location"]):
            # The same employer requisition listed in another place: one vacancy, one more location.
            repo.add_location(existing["id"], city, raw.source_job_id, raw.url)
        # A detail call re-ingests the posting it just enriched: its contact fields and detail belong in the row.
        later = {k: v for k, v in _source_raw(raw).items() if k in ("contact_fields", "detail")}
        repo.merge_source_raw(raw.source, raw.source_job_id, later)
        _fill_gaps(repo, existing["id"], raw)
        return IngestOutcome(existing["id"], False, "source")

    by: Literal["url", "fingerprint", "group"] | None = None
    match = repo.find_by_canonical_url(url) if url else None
    if match is not None:
        by = "url"
    if match is None and not employer_posting:
        # Employer postings are identified by requisition or address only; company + title can describe
        # two distinct requisitions of the same employer.
        match = _unambiguous(repo, repo.find_all_by_fingerprint(fingerprint(raw.company, raw.title, city)))
        by = "fingerprint" if match is not None else None
    if match is None and not employer_posting:
        match = _unambiguous(repo, repo.find_all_by_group_key(group_key(raw.company, raw.title), GROUP_WINDOW_DAYS, now))
        by = "group" if match is not None else None

    if match is not None:
        job_id = match["id"]
        repo.add_source(job_id, raw.source, raw.source_job_id, raw.url, _source_raw(raw))
        if by == "group" and city and norm_text(city) != norm_text(match["location"]):
            _link_city(repo, match, raw, city, home_city)
        _fill_gaps(repo, job_id, raw)
        return IngestOutcome(job_id, False, by)

    fields = _job_fields(raw)
    job_id = repo.insert_job(**fields)
    repo.add_source(job_id, raw.source, raw.source_job_id, raw.url, _source_raw(raw))
    for extra in raw.locations:
        if extra and norm_text(extra) != norm_text(city):
            repo.add_location(job_id, extra)
    return IngestOutcome(job_id, True, None)


def _link_city(repo: Repo, job, raw: RawJob, city: str, home_city: str | None) -> None:
    """Attach another city; make it primary if it is closer to the searched location."""
    current = job["location"]
    new_d, cur_d = distance_km(home_city, city), distance_km(home_city, current)
    if new_d is not None and (cur_d is None or new_d < cur_d):
        if current:
            repo.add_location(job["id"], current, None, job["job_url"])
        repo.update_job(
            job["id"],
            location=city,
            fingerprint=fingerprint(raw.company, raw.title, city),
            job_url=raw.url or job["job_url"],
            canonical_url=canonical_url(raw.url) if raw.url else job["canonical_url"],
        )
    else:
        repo.add_location(job["id"], city, raw.source_job_id, raw.url)


# --- alert cards ------------------------------------------------------------------------


def card_to_raw(card: AlertCard, resolved: ResolvedLink | None = None, search_profile: str | None = None) -> RawJob:
    """Convert an alert card; StepStone cards pass the resolved link (or None if resolving failed)."""
    job_id = card.source_job_id
    url = card.url
    if card.source.startswith("stepstone"):
        job_id = resolved.job_id if resolved and resolved.ok else None
        url = resolved.url if resolved and resolved.ok else None  # never keep the tracking link
    return RawJob(
        source=card.source,
        source_job_id=job_id,
        title=card.title,
        company=card.company or "(company not in the alert email)",
        location=card.locations[0] if card.locations else None,
        locations=list(card.locations),
        url=url,
        salary_estimate_min=card.salary_estimate_min,
        salary_estimate_max=card.salary_estimate_max,
        work_mode=card.work_mode,
        working_time=card.working_time,
        contract_type=card.contract_type,
        raw={"title_truncated": card.title_truncated, "badges": list(card.badges), "section": card.section,
             **({"company_unknown": True} if card.company_unknown else {}),
             **({"email_excerpt": card.email_excerpt} if card.email_excerpt else {}),
             **({"message_id": card.message_id} if card.message_id else {}),
             **({"llm_extracted": True} if card.llm_extracted else {})},
        search_profile=search_profile,
        section=card.section,
    )
