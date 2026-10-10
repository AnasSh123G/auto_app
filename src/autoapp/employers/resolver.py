"""Cache-first employer identity resolution (verified site reuse, trusted links, bounded search)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from autoapp.config.models import EmployerCareersConfig
from autoapp.employers.budget import PageMeter
from autoapp.employers.gateway import SearchGateway, SearchUnavailableForRun
from autoapp.employers.hints import build_search
from autoapp.employers.names import legal_form, normalize_name
from autoapp.employers.parallel import SearchError
from autoapp.employers.repo import EmployerRepo
from autoapp.employers.scope import bare_host, is_directory, is_shared_root
from autoapp.employers.verify import Candidate, Verification, is_unnamed_client, verify_candidates
from autoapp.fetch.http_fetcher import FetchResult

Fetch = Callable[[str], FetchResult]
Emit = Callable[..., None]


@dataclass
class Seed:
    """A stored alert job that may lead to an employer's careers site."""

    job_id: str
    company: str
    title: str
    city: str | None = None
    profile: str | None = None
    status: str = "new"
    description: str | None = None
    job_url: str | None = None
    application_url: str | None = None
    sources: list[str] = field(default_factory=list)


@dataclass
class Group:
    """Seeds of one employer name; processed once per run."""

    key: str
    name: str
    seeds: list[Seed]

    @property
    def city(self) -> str | None:
        return next((s.city for s in self.seeds if s.city), None)

    @property
    def titles(self) -> list[str]:
        return [s.title for s in self.seeds]

    @property
    def names(self) -> list[str]:
        return list(dict.fromkeys(s.company for s in self.seeds))


def group_seeds(seeds: list[Seed]) -> list[Group]:
    """Group by the normalized full name (legal form kept: ``Acme AG`` and ``Acme GmbH`` stay apart)."""
    groups: dict[str, Group] = {}
    for seed in seeds:
        key = normalize_name(seed.company)
        groups.setdefault(key, Group(key, seed.company, [])).seeds.append(seed)
    return list(groups.values())


def find_saved(repo: EmployerRepo, group: Group):  # noqa: ANN201
    """The one saved employer for this name, ``None``, or the string ``'ambiguous'``."""
    wanted = normalize_name(group.name)
    candidates = repo.candidates(group.name)
    exact = [c for c in candidates if any(a["norm"] == wanted for a in repo.aliases(c["id"]))]
    if exact:
        pool = exact
    elif candidates and not legal_form(group.name):
        pool = candidates  # an alert name without a legal form may mean any saved form of the name
    else:
        return None  # nothing saved, or only namesakes with another legal form
    if len(pool) == 1:
        return pool[0]
    if group.city:
        city = group.city.split(",")[0].strip().casefold()
        hinted = [c for c in pool if any(a["hint"] == city for a in repo.aliases(c["id"]))]
        if len(hinted) == 1:
            return hinted[0]
    return "ambiguous"


@dataclass
class Identity:
    status: str  # verified | ambiguous | unresolved | skipped | deferred
    employer_id: str | None = None
    source: str | None = None  # cache | trusted_link | search | user
    reason: str | None = None
    searches: int = 0
    pages: int = 0
    kind: str | None = None  # machine-readable reason: no_key, cap, auth, rate_limited, failure_cached, ...


PAGES_USED_UP = ("the page limit for this run is used up (sources.employer_careers.max_pages_per_run); "
                 "it is looked up in the next run")


class IdentityResolver:
    def __init__(self, repo: EmployerRepo, config: EmployerCareersConfig, fetch: Fetch, meter: PageMeter,
                 gateway: SearchGateway | None, emit: Emit, *, now: Callable[[], datetime] | None = None) -> None:
        self.repo, self.config, self.fetch, self.meter = repo, config, fetch, meter
        self.gateway, self.emit = gateway, emit
        self._now = now or (lambda: datetime.now(UTC))

    # --- cache -------------------------------------------------------------------------------

    def _saved(self, group: Group):  # noqa: ANN202
        return find_saved(self.repo, group)

    def _remember_alias(self, employer_id: str, group: Group) -> None:
        city = (group.city or "").split(",")[0].strip()
        for name in group.names:
            self.repo.add_alias(employer_id, name, hint=city.casefold(), source="alert")

    # --- main --------------------------------------------------------------------------------

    def resolve(self, group: Group, *, again: bool = False, refresh_site: bool = False, allow_search: bool = True) -> Identity:
        if is_unnamed_client(group.name):
            return Identity("skipped", reason="the alert does not name the employer", kind="unnamed_client")
        saved = self._saved(group)
        if saved == "ambiguous":
            return Identity("ambiguous", reason="several saved employers share this name; confirm one with --website",
                            kind="saved_ambiguous")
        if saved is not None:
            if saved["status"] == "verified" and not refresh_site:
                self._remember_alias(saved["id"], group)
                return Identity("verified", saved["id"], "cache", kind="cache")
            if saved["status"] in ("ambiguous", "unresolved") and not again and not refresh_site \
                    and self.repo.failure_is_fresh(saved, self.config.failure_ttl_hours, self._now()):
                return Identity(saved["status"], saved["id"], "cache",
                                f"{saved['failure_reason']} (not looked up again before {self.config.failure_ttl_hours} hours pass)",
                                kind="failure_cached")
        return self._lookup(group, saved, refresh=refresh_site, allow_search=allow_search)

    def _trusted_candidates(self, group: Group) -> list[Candidate]:
        out: list[Candidate] = []
        for seed in group.seeds:
            for url in (seed.application_url, seed.job_url):
                if url and not is_directory(url) and not is_shared_root(url) and url.startswith(("http://", "https://")):
                    out.append(Candidate(url))
        return out

    def _verify(self, group: Group, candidates: list[Candidate]) -> Verification:
        title = next(iter(group.titles), None)
        return verify_candidates(self.meter.wrap(self.fetch), candidates, names=group.names, city=group.city,
                                 title=title, max_pages=self.meter.left)

    def _out_of_pages(self) -> bool:
        """The run's page budget (``max_pages_per_run``) is used up: nothing more can be checked in this run."""
        return self.meter.run.pages <= 0

    def _lookup(self, group: Group, saved, *, refresh: bool, allow_search: bool) -> Identity:  # noqa: ANN001
        result = Identity("unresolved", searches=0)
        outcome: Verification | None = None
        if self._out_of_pages():
            # Searching now would be paid for and then not checked; the employer is not at fault either.
            return self._finish(group, saved, None, result, deferred=PAGES_USED_UP, kind="page_limit")
        source = "trusted_link"
        trusted = self._trusted_candidates(group)
        if trusted and not refresh:
            outcome = self._verify(group, trusted)
        if outcome is None or outcome.status != "verified":
            source = "search"
            if not allow_search or self.gateway is None:
                return self._finish(group, saved, outcome, result, deferred="search is off for this command", kind="search_off")
            for attempt in (0, 1):
                objective, queries = build_search(group.name, title=group.titles[0] if group.titles else None,
                                                  city=group.city, refined=attempt == 1)
                try:
                    response = self.gateway.search(group.key, objective, queries)
                except SearchUnavailableForRun as stop:
                    return self._finish(group, saved, outcome, result, deferred=stop.reason, kind=stop.kind)
                except SearchError as err:
                    return self._finish(group, saved, outcome, result, deferred=f"search failed: {err}", kind=err.kind)
                result.searches += 1
                candidates = [Candidate(h.url, h.title, tuple(h.excerpts)) for h in response.results]
                outcome = self._verify(group, candidates)
                if outcome.status in ("verified", "ambiguous"):
                    break
                if self._out_of_pages():  # the check stopped for lack of pages, not for lack of evidence
                    return self._finish(group, saved, outcome, result, deferred=PAGES_USED_UP, kind="page_limit")
        return self._finish(group, saved, outcome, result, source=source, refresh=refresh)

    def _finish(self, group: Group, saved, outcome: Verification | None, result: Identity, *,  # noqa: ANN001
                source: str | None = None, deferred: str | None = None, kind: str | None = None,
                refresh: bool = False) -> Identity:
        result.pages = self.meter.used
        if deferred is not None:
            # Not the employer's fault: nothing is negatively cached, so a later run can look it up.
            result.status, result.reason, result.kind = "deferred", deferred, kind
            if outcome is not None and outcome.reason:
                result.reason = f"{deferred} ({outcome.reason})"
            return result
        assert outcome is not None
        employer_id = saved["id"] if saved is not None else None
        if outcome.status == "verified" and outcome.website:
            if employer_id is None:
                employer_id = self.repo.create_employer(group.name, status="verified", website=outcome.website,
                                                        provenance=source, evidence=outcome.evidence)
            elif saved is not None and saved["pinned"] and saved["website"] and bare_host(saved["website"]) != bare_host(outcome.website):
                self.repo.record_failure(employer_id, f"refresh proposes {outcome.website}; confirm it with --website")
                return Identity("verified", employer_id, "user", "kept the site you confirmed; a different site was proposed",
                                result.searches, result.pages, kind="proposal_pending")
            else:
                self.repo.verify(employer_id, outcome.website, provenance=source or "search", evidence=outcome.evidence)
            self.repo.add_site(employer_id, outcome.website, outcome.website, kind="website",
                               evidence=outcome.evidence)
            self._remember_alias(employer_id, group)
            result.status, result.employer_id, result.source, result.kind = "verified", employer_id, source, source
            return result
        status = "ambiguous" if outcome.status == "ambiguous" else "unresolved"
        if employer_id is None:
            employer_id = self.repo.create_employer(group.name, status=status, evidence=outcome.evidence)
            self._remember_alias(employer_id, group)
        reason = outcome.reason or "no verified website"
        old_site = saved["website"] if saved is not None and saved["status"] == "verified" and not saved["pinned"] else None
        if refresh and old_site and f"{bare_host(old_site)}: not readable" not in reason:
            # The user asked to look the site up again and the new evidence does not support it (a site that
            # was merely unreachable keeps its status).
            self.repo.withdraw(employer_id, status, reason, outcome.evidence)
        else:
            self.repo.record_failure(employer_id, reason, status=status, evidence=outcome.evidence)
        result.status, result.employer_id, result.reason, result.kind = status, employer_id, outcome.reason, status
        return result

    def confirm(self, group: Group, website: str) -> Identity:
        """Record a site the user confirmed for this employer; it is pinned and never replaced silently."""
        origin = website if website.endswith("/") else website + "/"
        saved = self._saved(group)
        employer_id = saved["id"] if saved not in (None, "ambiguous") else None
        evidence = {"confirmed_by": "user", "at": self._now().isoformat(timespec="seconds")}
        if employer_id is None:
            employer_id = self.repo.create_employer(group.name, status="verified", website=origin, provenance="user",
                                                    evidence=evidence, pinned=True)
        else:
            self.repo.verify(employer_id, origin, provenance="user", evidence=evidence, pinned=True)
            self.repo.drop_sites(employer_id)
        self.repo.add_site(employer_id, origin, origin, kind="website", evidence=evidence)
        self._remember_alias(employer_id, group)
        return Identity("verified", employer_id, "user", kind="user")
