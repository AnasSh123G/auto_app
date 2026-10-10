"""Match an alert job to a verified employer posting, conservatively.

Priority: (1) the same employer-scoped requisition reference; (2) otherwise a unique full-title
match with affirmative, compatible location evidence. Conflicting references, several plausible
postings or title-only similarity stay unresolved: another role never substitutes for the alert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from autoapp.discovery.normalize import norm_text
from autoapp.employers.listings import ref_from_text
from autoapp.employers.names import core_name
from autoapp.employers.repo import Listing

_REMOTE = ("remote", "homeoffice", "home office", "mobiles arbeiten")
# LinkedIn writes regions and English city names ("Greater Munich Metropolitan Area"); sites use German ones.
_REGION = re.compile(r"^(?:greater\s+)?(.+?)(?:\s+(?:metropolitan\s+area|metropolregion|area|region))?$")
_CITY_ALIASES = {
    "munich": "muenchen", "cologne": "koeln", "nuremberg": "nuernberg", "dusseldorf": "duesseldorf",
    "hanover": "hannover", "brunswick": "braunschweig", "frankfurt am main": "frankfurt", "frankfurt main": "frankfurt",
    "frankfurt a m": "frankfurt", "freiburg im breisgau": "freiburg", "halle saale": "halle",
}


def place_key(value: str | None) -> str:
    """Normalised city of a location string: ``Greater Munich Metropolitan Area`` -> ``muenchen``."""
    text = norm_text((value or "").split(",")[0])
    match = _REGION.match(text)
    text = match.group(1) if match else text
    return _CITY_ALIASES.get(text, text)


def title_key(title: str | None, company: str | None = None) -> str:
    """Normalised title without gender tags and without a trailing ``für die FEAG Gruppe``/``bei mittwald``
    that only repeats the employer's name."""
    text = norm_text(title)
    core = norm_text(core_name(company or ""))
    if core:
        text = re.sub(rf"\s+(?:fuer|bei|at|von|@)\s+(?:(?:die|der|den|dem|das|the)\s+)?{re.escape(core)}"
                      rf"(?:\s+(?:gruppe|group|gmbh|ag|se))?$", "", text)
    return text


@dataclass
class SeedFacts:
    title: str
    city: str | None = None
    work_mode: str | None = None
    description: str | None = None
    title_truncated: bool = False
    ref: str | None = None  # requisition shown in the alert/ad itself, if any
    company: str | None = None


@dataclass
class MatchResult:
    outcome: str  # matched | unresolved | none
    listing: Listing | None = None
    basis: str | None = None  # reference | title_location
    reason: str | None = None
    candidates: list[str] = field(default_factory=list)


def _ref_key(ref: str | None) -> str:
    return "".join(ch for ch in (ref or "").casefold() if ch.isalnum())


def location_evidence(seed: SeedFacts, listing: Listing) -> str:
    """``affirmative`` (same place), ``conflict`` (known and different) or ``unknown``."""
    wanted = place_key(seed.city)
    places = [p for p in (place_key(loc) for loc in listing.locations) if p]
    if not wanted or not places:
        return "unknown"
    if any(p == wanted or wanted in p.split() or p in wanted.split() for p in places):
        return "affirmative"
    return "conflict"


def _mode_conflict(seed: SeedFacts, listing: Listing) -> bool:
    a, b = norm_text(seed.work_mode), norm_text(listing.work_mode)
    if not a or not b:
        return False
    return (any(w in a for w in _REMOTE)) != (any(w in b for w in _REMOTE))


def seed_facts_from_job(job, *, title_truncated: bool = False) -> SeedFacts:  # noqa: ANN001 - sqlite row
    return SeedFacts(title=job["title"], city=job["location"], work_mode=job["work_mode"],
                     description=job["description"], title_truncated=title_truncated,
                     ref=ref_from_text(job["description"] or ""), company=job["company"])


def match_seed(seed: SeedFacts, listings: list[Listing]) -> MatchResult:
    wanted = title_key(seed.title, seed.company)
    if not wanted:
        return MatchResult("unresolved", reason="the alert has no usable title")
    # 1. Same requisition reference.
    if seed.ref:
        wanted_ref = _ref_key(seed.ref)
        same = [item for item in listings if _ref_key(item.ref) == wanted_ref and wanted_ref]
        if same:
            places = same if len({place_key(" ".join(i.locations)) for i in same}) <= 1 else sorted(
                same, key=lambda i: location_evidence(seed, i) != "affirmative")
            return MatchResult("matched", places[0], "reference", candidates=[i.url for i in same])
    # 2. Title evidence. Postings whose known reference conflicts with the alert's are not candidates.
    def conflicting_ref(item: Listing) -> bool:
        return bool(seed.ref and item.ref and _ref_key(item.ref) != _ref_key(seed.ref))

    def key(item: Listing) -> str:
        return title_key(item.title, seed.company)

    title_matches = [i for i in listings if key(i) == wanted]
    if not title_matches and seed.title_truncated and len(wanted) >= 12:
        title_matches = [i for i in listings if key(i).startswith(wanted)]
        if len({key(i) for i in title_matches}) > 1:
            return MatchResult("unresolved", reason="the truncated title fits several different postings",
                               candidates=[i.url for i in title_matches])
    if not title_matches:
        return MatchResult("none", reason="no posting with this title")
    usable = [i for i in title_matches if not conflicting_ref(i) and not _mode_conflict(seed, i)]
    if not usable:
        return MatchResult("unresolved", reason="matching postings have a different requisition or work mode",
                           candidates=[i.url for i in title_matches])
    # One requisition listed in several places is one vacancy, judged by its best location evidence.
    rank = {"affirmative": 0, "unknown": 1, "conflict": 2}
    groups: dict[str, list[Listing]] = {}
    for n, item in enumerate(usable):
        groups.setdefault(_ref_key(item.ref) or f"single-{n}", []).append(item)
    logical: list[Listing] = []
    evidence: dict[int, str] = {}
    for members in groups.values():
        best = min(members, key=lambda i: rank[location_evidence(seed, i)])
        logical.append(best)
        evidence[id(best)] = location_evidence(seed, best)
    plausible = [i for i in logical if evidence[id(i)] != "conflict"]
    if not plausible:
        return MatchResult("unresolved", reason="postings with this title are in other places",
                           candidates=[i.url for i in title_matches])
    if len(plausible) > 1:
        return MatchResult("unresolved", reason="several postings fit the alert equally well",
                           candidates=[i.url for i in plausible])
    chosen = plausible[0]
    if evidence[id(chosen)] != "affirmative":
        return MatchResult("unresolved", reason="the title matches but nothing confirms the location",
                           candidates=[chosen.url])
    if seed.title_truncated and key(chosen) != wanted and not seed.ref:
        return MatchResult("unresolved", reason="a truncated title needs a reference to be confirmed",
                           candidates=[chosen.url])
    return MatchResult("matched", chosen, "title_location", candidates=[chosen.url])
