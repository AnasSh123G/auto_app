"""Attach verified employer postings to alert jobs and import other relevant openings.

Nothing here closes, rewrites or re-evaluates a user's job: provenance is added, missing details are
filled only before evaluation, and only a recovered unevaluated ``description_unavailable`` job returns
to ``new``. Prepared packages, edited notes, evaluations and valid found forms are never touched.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from autoapp.assist.filler import is_restricted
from autoapp.config.models import SearchProfile
from autoapp.db.repo import Repo
from autoapp.db.status import Status
from autoapp.discovery.ingest import EMPLOYER_SOURCE, ingest, job_requisitions
from autoapp.discovery.normalize import canonical_url, norm_text
from autoapp.discovery.prefilter import Candidate, check
from autoapp.employers.match import MatchResult, match_seed, seed_facts_from_job
from autoapp.employers.repo import EmployerRepo, Listing
from autoapp.employers.scope import is_directory
from autoapp.sources.models import RawJob

ENRICHABLE = frozenset({Status.NEW.value, Status.DESCRIPTION_UNAVAILABLE.value})
_LEVEL_WORDS = frozenset({"junior", "senior", "werkstudent", "trainee", "praktikant", "praktikum", "intern", "lead",
                          "mid", "level", "berufseinsteiger", "absolvent"})
Emit = Callable[..., None]


def posting_source_id(employer_id: str, listing: Listing) -> str:
    key = listing.ref or canonical_url(listing.url)
    return f"emp:{employer_id}:{key}"


@dataclass
class SeedOutcome:
    job_id: str
    outcome: str  # matched | conflict | unresolved | not_found | not_found_partial
    posting_url: str | None = None
    reason: str | None = None
    recovered: bool = False
    entry_replaced: bool = False


@dataclass
class RelatedSummary:
    created: list[str] = field(default_factory=list)
    linked: int = 0
    unrelated: int = 0
    filtered: int = 0
    closed: int = 0
    skipped_attached: int = 0


class Reconciler:
    def __init__(self, repo: Repo, emp: EmployerRepo, emit: Emit, *, restricted_domains: list[str],
                 today: date | None = None) -> None:
        self.repo, self.emp, self.emit = repo, emp, emit
        self.restricted = restricted_domains
        self.today = today or date.today()

    # --- exact vacancy -----------------------------------------------------------------------

    def reconcile_seed(self, employer, job, listings: list[Listing], *, scan_partial: bool) -> SeedOutcome:  # noqa: ANN001
        truncated = any(json.loads(s["raw"] or "{}").get("title_truncated") for s in self.repo.sources(job["id"]))
        result = match_seed(seed_facts_from_job(job, title_truncated=bool(truncated)), listings)
        if result.outcome == "matched" and result.listing is not None:
            return self._attach(employer, job, result)
        if result.outcome == "none":
            reason = ("not seen in a partial scan; it may be on a page that was not read" if scan_partial
                      else "no posting with this title on the careers site; the vacancy may be closed")
            outcome = SeedOutcome(job["id"], "not_found_partial" if scan_partial else "not_found", reason=reason)
        else:
            outcome = SeedOutcome(job["id"], "unresolved", reason=result.reason)
            self.emit("employer_unresolved", result.reason or "", job_id=job["id"])
        self.emp.record_seed(job["id"], outcome.outcome, employer_id=employer["id"], reason=outcome.reason,
                             candidates=result.candidates)
        return outcome

    def _safe_entry(self, listing: Listing) -> str:
        apply_url = listing.apply_url
        if apply_url and not is_restricted(apply_url, self.restricted) and not is_directory(apply_url):
            return apply_url
        return listing.url

    def _attach(self, employer, job, result: MatchResult) -> SeedOutcome:  # noqa: ANN001
        listing = result.listing
        assert listing is not None
        job_id = job["id"]
        sid = posting_source_id(employer["id"], listing)
        held = job_requisitions(self.repo, job_id)
        if held and sid not in held:
            self._flag_conflict(job_id, held | {sid})
            outcome = SeedOutcome(job_id, "conflict", listing.url, "the job already holds a different employer requisition")
            self.emp.record_seed(job_id, "conflict", employer_id=employer["id"], posting_url=listing.url,
                                 posting_ref=listing.ref, reason=outcome.reason)
            return outcome
        owner = self.repo.find_by_source(EMPLOYER_SOURCE, sid)
        note = None
        if owner is not None and owner["id"] != job_id:
            note = f"the posting is also stored as job {owner['id']}"  # never merge two existing jobs silently
        else:
            self.repo.add_source(job_id, EMPLOYER_SOURCE, sid, listing.url, {
                "employer_id": employer["id"], "posting_ref": listing.ref, "basis": result.basis,
                "locations": listing.locations,
            })
        recovered = replaced = False
        replaced_url = None
        evaluated = self.repo.current_evaluation(job_id) is not None
        if job["status"] in ENRICHABLE and not evaluated:
            updates: dict = {}
            if not (job["description"] or "").strip() and listing.description:
                updates["description"] = listing.description
            for key, value in (("work_mode", listing.work_mode), ("ad_salary_min", listing.salary_min),
                               ("ad_salary_max", listing.salary_max)):
                if value is not None and job[key] in (None, ""):
                    updates[key] = value
            app = self.repo.get_application(job_id)
            found_form = app is not None and app["form_status"] == "found"
            current = job["application_url"]
            platform_only = (not current or is_restricted(current, self.restricted) or is_directory(current)
                             or current == job["job_url"])
            if platform_only and not found_form:
                updates["application_url"] = self._safe_entry(listing)
                replaced, replaced_url = True, current
            if updates:
                self.repo.update_job(job_id, **updates)
            recovered = "description" in updates
            if job["status"] == Status.DESCRIPTION_UNAVAILABLE.value and recovered:
                self.repo.transition(job_id, Status.NEW, note="description recovered from the employer posting")
        self.emp.record_seed(job_id, "matched", employer_id=employer["id"], posting_url=listing.url,
                             posting_ref=listing.ref, reason=note or f"matched by {result.basis}",
                             replaced_application_url=replaced_url)
        self.emit("employer_matched", listing.url, job_id=job_id, recovered=recovered)
        return SeedOutcome(job_id, "matched", listing.url, note, recovered, replaced)

    def _flag_conflict(self, job_id: str, refs: set[str]) -> None:
        job = self.repo.get_job(job_id)
        reasons = json.loads(job["needs_review_reasons"] or "[]") if job and job["needs_review_reasons"] else []
        text = "employer requisitions conflict: " + ", ".join(sorted(r.rsplit(":", 1)[-1] for r in refs))
        if text not in reasons:
            self.repo.set_needs_review(job_id, [*reasons, text])

    def flag_reference_conflicts(self) -> list[str]:
        """Flag (never split) jobs that hold more than one employer requisition from earlier merges."""
        flagged = []
        for row in self.repo.conn.execute("SELECT DISTINCT job_id FROM job_sources WHERE source = ?", (EMPLOYER_SOURCE,)):
            refs = job_requisitions(self.repo, row[0])
            if len(refs) > 1:
                self._flag_conflict(row[0], refs)
                flagged.append(row[0])
        return flagged

    # --- additional openings -----------------------------------------------------------------

    def import_related(self, employer, listings: list[Listing], profiles: list[SearchProfile], *,  # noqa: ANN001
                       attached_urls: set[str], seed_job_ids: list[str]) -> RelatedSummary:
        out = RelatedSummary()
        for listing in listings:
            if listing.url in attached_urls:
                out.skipped_attached += 1
                continue
            if listing.expires_at and listing.expires_at[:10] < self.today.isoformat():
                out.closed += 1
                continue
            matched, hit_any = [], False
            for profile in profiles:
                if not keyword_hit(profile, listing):
                    continue
                hit_any = True
                if check(candidate_of(listing, employer["display_name"]), profile, today=self.today) is None:
                    matched.append(profile)
            if not matched:
                out.filtered += 1 if hit_any else 0
                out.unrelated += 0 if hit_any else 1
                continue
            sid = posting_source_id(employer["id"], listing)
            raw = RawJob(
                source=EMPLOYER_SOURCE, source_job_id=sid, title=listing.title, company=employer["display_name"],
                location=listing.locations[0] if listing.locations else None, locations=list(listing.locations),
                url=listing.url, application_url=listing.apply_url, description=listing.description,
                posted_at=date.fromisoformat(listing.posted_at[:10]) if listing.posted_at else None,
                ad_salary_min=listing.salary_min, ad_salary_max=listing.salary_max, work_mode=listing.work_mode,
                raw={"employer_id": employer["id"], "posting_ref": listing.ref,
                     "profiles": [p.name for p in matched], "seed_jobs": seed_job_ids},
                search_profile=matched[0].name,
            )
            outcome = ingest(self.repo, raw, home_city=matched[0].locations[0].name if matched[0].locations else None)
            if outcome.created:
                out.created.append(outcome.job_id)
                self.emit("employer_job_added", listing.title, job_id=outcome.job_id, employer=employer["display_name"])
            else:
                out.linked += 1
        return out


def candidate_of(listing: Listing, company: str) -> Candidate:
    from datetime import date as _date

    return Candidate(title=listing.title, company=company,
                     posted_at=_date.fromisoformat(listing.posted_at[:10]) if listing.posted_at else None,
                     ad_salary_max=listing.salary_max, work_mode=listing.work_mode)


def _tokens(text: str) -> list[str]:
    return norm_text(text).split()


def keyword_hit(profile: SearchProfile, listing: Listing) -> bool:
    """A configured keyword phrase appears in the title, or two different phrases in the description."""
    title = _tokens(listing.title)
    title_set = set(title)
    for phrase in profile.keywords:
        words = [w for w in _tokens(phrase) if w not in _LEVEL_WORDS] or _tokens(phrase)
        if words and set(words) <= title_set:
            return True
    description = f" {norm_text(listing.description or '')[:6000]} "
    hits = {p for p in profile.keywords if norm_text(p) and f" {norm_text(p)} " in description}
    return len(hits) >= 2


def wanted_priority(titles: list[str], profiles: list[SearchProfile]) -> Callable[[object], int | None]:
    """Detail-fetch priority: the alert's own titles first, then keyword-matching titles, else skip."""
    wanted = {norm_text(t) for t in titles}

    def priority(link) -> int | None:  # noqa: ANN001 - listings.ListingLink
        if norm_text(link.title) in wanted:
            return 0
        probe = Listing(url=link.url, title=link.title)
        if any(keyword_hit(p, probe) for p in profiles):
            return 1
        return None

    return priority
