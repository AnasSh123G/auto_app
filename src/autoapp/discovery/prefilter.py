"""Rule-based pre-filter that runs before any LLM call or job-page fetch.

``check`` returns the reason a listing is dropped, or ``None`` to keep it. A rule applies
only when the search profile configures it, and a value the listing does not state (or
that we cannot classify) never drops it. Platform salary estimates are never an input.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

from autoapp.config.models import SearchProfile
from autoapp.discovery.normalize import norm_company, norm_text
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.models import RawJob


class Candidate(BaseModel):
    """The listing facts the pre-filter looks at, from either a source job or an alert card."""

    title: str
    company: str
    posted_at: date | None = None
    ad_salary_max: int | None = None  # employer-stated annual EUR only
    work_mode: str | None = None
    working_time: str | None = None
    contract_type: str | None = None  # free card text or "permanent" / "fixed_term"

    @classmethod
    def from_raw_job(cls, job: RawJob) -> Candidate:
        return cls(
            title=job.title,
            company=job.company,
            posted_at=job.posted_at,
            ad_salary_max=job.ad_salary_max,
            work_mode=job.work_mode,
            working_time=job.working_time,
            contract_type=job.contract_type,
        )

    @classmethod
    def from_alert_card(cls, card: AlertCard) -> Candidate:
        # Cards carry only platform estimates, which never filter, and no posting date.
        return cls(
            title=card.title,
            company=card.company,
            work_mode=card.work_mode,
            working_time=card.working_time,
            contract_type=card.contract_type,
        )


# ------------------------------------------------------------------ classification

# Patterns run on norm_text output (lowercase, umlauts folded, punctuation -> space).
_CONTRACT_KINDS: dict[str, re.Pattern[str]] = {
    "permanent": re.compile(r"\b(?:permanent|unbefristet\w*|feste anstellung|festanstellung)\b"),
    "fixed_term": re.compile(r"\b(?:fixed[ _]term|befristet\w*|zeitvertrag|temporary)\b"),
}  # fmt: skip
_POSITION_TYPES: dict[str, re.Pattern[str]] = {
    "training": re.compile(r"\b(?:ausbildung|duales studium|dualen studium|studium|trainee)\b"),
    "internship": re.compile(r"\b(?:praktikum|praktikant\w*|internship)\b"),
    "working_student": re.compile(r"\b(?:werkstudent\w*|working student)\b"),
    "freelance": re.compile(
        r"\b(?:freie mitarbeit|freie mitarbeiter\w*|projektmitarbeit|freelance\w*|freiberuf\w*)\b"
    ),
}
# Aliases a user may write in ``exclude_position_types`` besides the canonical names.
_POSITION_ALIASES = {
    "ausbildung": "training", "studium": "training", "trainee": "training",
    "praktikum": "internship", "werkstudent": "working_student",
    "freie mitarbeit": "freelance", "projektmitarbeit": "freelance", "freiberuflich": "freelance",
}  # fmt: skip

_WORK_MODES: dict[str, re.Pattern[str]] = {
    "remote": re.compile(r"\b(?:remote|vollremote|fully remote)\b"),
    "hybrid": re.compile(r"\b(?:hybrid|teilweise remote|home office\w*|homeoffice\w*)\b"),
    "onsite": re.compile(r"\b(?:onsite|on site|vor ort|praesenz\w*)\b"),
}
_WORKING_TIMES: dict[str, re.Pattern[str]] = {
    "full_time": re.compile(r"\b(?:full[ _]time|vollzeit)\b"),
    "part_time": re.compile(r"\b(?:part[ _]time|teilzeit)\b"),
}


def _kinds(text: str | None, table: dict[str, re.Pattern[str]]) -> set[str]:
    normalised = norm_text(text)
    return {kind for kind, pattern in table.items() if pattern.search(normalised)} if normalised else set()


def _contract_kinds(text: str | None) -> set[str]:
    kinds = _kinds(text, _CONTRACT_KINDS)
    if "freelance" in _kinds(text, _POSITION_TYPES):
        kinds.add("freelance")
    return kinds


def _phrase_in(needle: str, haystack: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


# ------------------------------------------------------------------------- rules


def _keyword_reason(c: Candidate, profile: SearchProfile) -> str | None:
    title = norm_text(c.title)
    for keyword in profile.exclude_keywords or []:
        if _phrase_in(norm_text(keyword), title):
            return f"excluded keyword: {keyword}"
    return None


def _company_reason(c: Candidate, profile: SearchProfile) -> str | None:
    company = norm_company(c.company)
    for name in profile.exclude_companies or []:
        blocked = norm_company(name)
        if blocked and (blocked == company or _phrase_in(blocked, company)):
            return f"excluded company: {name}"
    return None


def _age_reason(c: Candidate, profile: SearchProfile, today: date) -> str | None:
    limit = profile.max_posting_age_days
    if limit is None or c.posted_at is None:
        return None
    age = (today - c.posted_at).days
    return f"posted {age} days ago (limit {limit})" if age > limit else None


def _salary_reason(c: Candidate, profile: SearchProfile) -> str | None:
    minimum = profile.min_salary
    if minimum is None or c.ad_salary_max is None:
        return None
    if c.ad_salary_max < minimum:
        return f"salary up to {c.ad_salary_max} EUR is below the minimum of {minimum}"
    return None


def _work_mode_reason(c: Candidate, profile: SearchProfile) -> str | None:
    wanted = profile.work_mode
    if wanted not in ("remote", "onsite"):
        # ``hybrid`` and ``any`` never drop: a hybrid seeker can live with a mostly-remote or
        # mostly-onsite job, and listings rarely say how much of either, so we leave it to the LLM.
        return None
    kinds = _kinds(c.work_mode, _WORK_MODES)
    # Only a clear, unambiguous single mode counts. Hybrid is compatible with both remote
    # and onsite profiles, and so is any multi-valued text.
    if len(kinds) != 1:
        return None
    (actual,) = kinds
    if (wanted, actual) in {("remote", "onsite"), ("onsite", "remote")}:
        return f"work mode {actual} does not match the wanted {wanted}"
    return None


def _position_reason(c: Candidate, profile: SearchProfile) -> str | None:
    text = norm_text(c.contract_type)
    if not text:
        return None
    found = _kinds(c.contract_type, _POSITION_TYPES)
    for configured in profile.exclude_position_types or []:
        key = norm_text(configured).replace(" ", "_")
        canonical = key if key in _POSITION_TYPES else _POSITION_ALIASES.get(norm_text(configured))
        if canonical in found or (canonical is None and _phrase_in(norm_text(configured), text)):
            return f"excluded position type: {configured}"
    return None


def _contract_reason(c: Candidate, profile: SearchProfile) -> str | None:
    wanted = profile.contract_type
    if wanted not in ("permanent", "fixed_term"):
        return None
    kinds = _contract_kinds(c.contract_type)
    # A listing naming several kinds ("Feste Anstellung, Befristet") may still fit.
    if kinds and wanted not in kinds:
        return f"contract type {'/'.join(sorted(kinds))} does not match the wanted {wanted}"
    return None


def _working_time_reason(c: Candidate, profile: SearchProfile) -> str | None:
    wanted = profile.working_time
    if wanted not in ("full_time", "part_time"):
        return None
    kinds = _kinds(c.working_time, _WORKING_TIMES)
    if len(kinds) == 1 and wanted not in kinds:
        (actual,) = kinds
        return f"working time {actual} does not match the wanted {wanted}"
    return None


def check(candidate: Candidate, profile: SearchProfile, *, today: date) -> str | None:
    """Return why ``candidate`` is dropped for ``profile``, or ``None`` if it stays."""
    return (
        _keyword_reason(candidate, profile)
        or _company_reason(candidate, profile)
        or _age_reason(candidate, profile, today)
        or _salary_reason(candidate, profile)
        or _work_mode_reason(candidate, profile)
        or _position_reason(candidate, profile)
        or _contract_reason(candidate, profile)
        or _working_time_reason(candidate, profile)
    )
