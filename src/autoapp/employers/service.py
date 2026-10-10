"""Employer careers discovery: select alert jobs, resolve each employer once, scan, reconcile, import.

The one entry point is ``run_discovery``; ``run``, ``search`` and ``autoapp employers --discover`` all call
it. Expected failures (search, network, identity, unsupported layouts) become per-employer reasons and never
stop other employers or sources; an unexpected error is reported as an error, not as an empty success.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, get_args

from autoapp.assist.filler import is_restricted
from autoapp.config.models import SearchProfile
from autoapp.db.status import Status
from autoapp.discovery.prefilter import Candidate, check
from autoapp.employers.budget import PageMeter
from autoapp.employers.crawl import RunBudget
from autoapp.employers.gateway import SearchGateway
from autoapp.employers.guard import check_target, guarded_client
from autoapp.employers.match import match_seed, seed_facts_from_job
from autoapp.employers.parallel import ParallelSearch
from autoapp.employers.posting import MAX_PAGES, PostingHit, PostingMemo, PostingMiss, find_posting
from autoapp.employers.reconcile import ENRICHABLE, Reconciler, wanted_priority
from autoapp.employers.repo import EmployerRepo, Listing
from autoapp.employers.resolver import Group, IdentityResolver, Seed, group_seeds
from autoapp.employers.scanner import EmployerScanner, ScanReport
from autoapp.employers.scope import is_directory
from autoapp.employers.verify import is_unnamed_client
from autoapp.sources.alerts.models import AlertSource
from autoapp.steps.context import RunContext

# Alerts from platforms the app does not open (LinkedIn, Indeed, StepStone). Workwise jobs are applied to on
# Workwise itself, which the app can open, so they need no employer lookup.
ALERT_SOURCES = tuple(s for s in get_args(AlertSource) if not s.startswith("workwise"))
# Alert jobs worth looking for at the employer: nothing evaluated as unfit, filtered, sent or closed.
SEED_STATUSES = (Status.NEW, Status.DESCRIPTION_UNAVAILABLE, Status.EVALUATION_FAILED, Status.REVIEW, Status.APPROVED,
                 Status.PREPARED, Status.DRAFTED)
# Jobs whose own ad is searched by "<company> <job title>": platform alerts, and the Arbeitsagentur, whose job pages
# mostly send you on to the employer's site anyway (and do not work well in the app's browser).
OWN_AD_SOURCES = (*ALERT_SOURCES, "arbeitsagentur")
# Only jobs worth applying to (evaluated as fit or maybe), and ads whose text could not be read: their own ad gives it.
OWN_AD_STATUSES = (Status.DESCRIPTION_UNAVAILABLE, Status.REVIEW, Status.APPROVED, Status.PREPARED, Status.DRAFTED)


@dataclass
class EmployerReport:
    name: str
    employer_id: str | None
    identity: str  # verified | ambiguous | unresolved | skipped | deferred
    source: str | None = None
    searches: int = 0
    reason: str | None = None
    kind: str | None = None
    scan: ScanReport | None = None
    seeds: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    related_created: list[str] = field(default_factory=list)
    related_linked: int = 0


@dataclass
class DiscoverySummary:
    enabled: bool = False
    employers: list[EmployerReport] = field(default_factory=list)
    deferred_jobs: dict[str, str] = field(default_factory=dict)
    search_requests: int = 0
    search_failures: int = 0
    estimated_usd: float = 0.0
    search_stop: str | None = None
    pages: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def _ids(self, attr: str) -> list[str]:
        return [j for e in self.employers for j in getattr(e, attr)]

    @property
    def matched(self) -> int:
        return len(self._ids("matched"))

    @property
    def recovered(self) -> int:
        return len(self._ids("recovered"))

    @property
    def related_created(self) -> list[str]:
        return self._ids("related_created")

    def lines(self) -> list[str]:
        if not self.enabled:
            return []
        ident = Counter(e.identity if e.source != "cache" else "saved" for e in self.employers)
        scans = Counter((e.scan.status if e.scan else "none") for e in self.employers)
        parts = [f"{len(self.employers)} employers"]
        for key, label in (("saved", "using a saved site"), ("verified", "newly verified"), ("ambiguous", "ambiguous"),
                           ("unresolved", "unresolved"), ("deferred", "deferred"), ("skipped", "skipped")):
            if ident.get(key):
                parts.append(f"{ident[key]} {label}")
        out = ["employer sites: " + ", ".join(parts)]
        out.append(f"employer scans: {scans.get('complete', 0)} complete, {scans.get('partial', 0)} partial, "
                   f"{scans.get('reused', 0)} reused, {scans.get('failed', 0)} failed; {self.pages} pages read")
        out.append(f"alert vacancies: {self.matched} matched at the employer ({self.recovered} descriptions recovered), "
                   f"{len(self._ids('unresolved'))} unresolved, {len(self._ids('not_found'))} not found; "
                   f"{len(self.related_created)} new employer jobs added")
        out.append(f"employer search: {self.search_requests} requests ({self.search_failures} failed), "
                   f"estimated ${self.estimated_usd:.3f} gross" + (f"; stopped: {self.search_stop}" if self.search_stop else ""))
        for e in self.employers:
            if e.identity in ("ambiguous", "unresolved", "deferred") and e.reason:
                out.append(f"employer {e.name}: {e.identity}: {e.reason}")
            if e.scan and e.scan.status == "partial" and e.scan.reason:
                out.append(f"employer {e.name}: partial scan: {e.scan.reason}")
        if self.deferred_jobs:
            out.append(f"employer discovery deferred for {len(self.deferred_jobs)} alert jobs: "
                       + ", ".join(sorted(set(self.deferred_jobs.values()))))
        return out + [f"warning: {w}" for w in self.warnings] + [f"error: {e}" for e in self.errors]


def eligible_seed_rows(conn, *, job_ids: list[str] | None, profiles: list[SearchProfile],  # noqa: ANN001
                       again: bool, sources: tuple[str, ...] = ALERT_SOURCES,
                       statuses: tuple[Status, ...] = SEED_STATUSES) -> list[Any]:
    marks = ", ".join("?" for _ in sources)
    states = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT DISTINCT j.* FROM jobs j JOIN job_sources s ON s.job_id = j.id WHERE s.source IN ({marks}) "
        f"AND j.status IN ({states}) ORDER BY j.discovered_at, j.id",
        [*sources, *(s.value for s in statuses)],
    ).fetchall()
    names = {p.name for p in profiles}
    has_seeds = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'employer_seeds'").fetchone()
    out = []
    for row in rows:
        if job_ids is not None and row["id"] not in job_ids:
            continue
        if row["search_profile"] and row["search_profile"] not in names:
            continue
        seed = conn.execute("SELECT outcome FROM employer_seeds WHERE job_id = ?", (row["id"],)).fetchone() if has_seeds else None
        if seed is not None and seed["outcome"] == "matched" and not again and job_ids is None:
            continue
        out.append(row)
    return out


def seed_of(row) -> Seed:  # noqa: ANN001 - sqlite row
    return Seed(job_id=row["id"], company=row["company"], title=row["title"], city=row["location"],
                profile=row["search_profile"], status=row["status"], description=row["description"],
                job_url=row["job_url"], application_url=row["application_url"])


def _profiles_for(group: Group, profiles: list[SearchProfile]) -> list[SearchProfile]:
    named = {s.profile for s in group.seeds if s.profile}
    if not named or any(s.profile is None for s in group.seeds):
        return profiles
    return [p for p in profiles if p.name in named]


def make_fetch(ctx: RunContext) -> tuple[Callable, Callable[[], None]]:
    """Public GETs through the shared fetcher (cache, pacing, host blocks) with the target guard applied."""
    client = guarded_client()
    restricted = ctx.config.assist.restricted_domains

    def guard(url: str) -> str | None:
        return check_target(url, restricted)

    def fetch(url: str):  # noqa: ANN202
        return ctx.http.fetch_once(url, check=guard, client=client)

    return fetch, client.close


def run_discovery(ctx: RunContext, *, job_ids: list[str] | None = None, profile: str | None = None, again: bool = False,
                  refresh_site: bool = False, website: str | None = None, force: bool = False,
                  fetch: Callable | None = None, api: ParallelSearch | None = None, today: date | None = None,
                  now: Callable[[], datetime] | None = None) -> DiscoverySummary:
    cfg = ctx.config.sources.employer_careers
    summary = DiscoverySummary(enabled=cfg.enabled or force)
    if not summary.enabled:
        return summary
    profiles = [p for p in ctx.config.search_profiles if profile in (None, p.name)]
    rows = eligible_seed_rows(ctx.conn, job_ids=job_ids, profiles=profiles, again=again)
    if not rows:
        return summary
    groups = group_seeds([seed_of(r) for r in rows])
    close_fetch: Callable[[], None] = lambda: None  # noqa: E731
    if fetch is None:
        fetch, close_fetch = make_fetch(ctx)
    own_api = api is None
    api = api or ParallelSearch(os.environ.get("PARALLEL_API_KEY", ""))
    emit = _emitter(ctx)
    emp = EmployerRepo(ctx.conn)
    gateway = SearchGateway(ctx.conn, cfg, api, reserve=cfg.reserved_for_own_ad_search)
    run_budget = RunBudget(pages=cfg.max_pages_per_run, openings=cfg.max_openings_per_run)
    reconciler = Reconciler(ctx.repo, emp, emit, restricted_domains=ctx.config.assist.restricted_domains, today=today)
    try:
        for index, group in enumerate(groups):
            if index >= cfg.max_employers_per_run:
                for seed in group.seeds:
                    summary.deferred_jobs[seed.job_id] = "employer limit reached for this run"
                continue
            meter = PageMeter(run_budget, cfg.max_pages_per_employer)
            report = EmployerReport(group.name, None, "unresolved", seeds=[s.job_id for s in group.seeds])
            summary.employers.append(report)
            try:
                _process_group(ctx, group, report, summary, profiles, emp, gateway, reconciler, fetch, meter, run_budget,
                               emit, again=again, refresh_site=refresh_site, website=website, now=now)
            except Exception as err:  # noqa: BLE001 - one employer must not stop the others
                summary.errors.append(f"employer {group.name}: {type(err).__name__}: {err}")
                emit("error", f"employer discovery for {group.name} failed: {type(err).__name__}: {err}")
            summary.pages += meter.used
    finally:
        close_fetch()
        if own_api:
            api.close()
    summary.search_requests = gateway.usage.requests
    summary.search_failures = gateway.usage.failures
    summary.estimated_usd = gateway.usage.estimated_usd
    summary.search_stop = gateway.stop_reason if gateway.stop_kind in ("no_key", "auth", "rate_limited", "cap") else None
    summary.warnings += [w for w in gateway.usage.warnings[:3]]
    reconciler.flag_reference_conflicts()
    emit("employer_search_usage", f"{summary.search_requests} search requests", requests=summary.search_requests,
         failures=summary.search_failures, estimated_usd=summary.estimated_usd)
    emit("employer_discovery_done", f"{len(summary.employers)} employers", employers=len(summary.employers))
    return summary


_SOURCE_WORDS = {"cache": "saved site", "search": "new lookup", "trusted_link": "link from the alert",
                 "user": "confirmed by you"}


def resolved_text(name: str, status: str, source: str | None) -> str:
    """The feed text of an employer lookup: ``FEAG GmbH: website verified (saved site)``."""
    how = f" ({_SOURCE_WORDS.get(source, source)})" if source else ""
    return {
        "verified": f"{name}: website verified{how}",
        "ambiguous": f"{name}: several companies share this name, none used{how}",
        "unresolved": f"{name}: no website could be verified{how}",
        "deferred": f"{name}: lookup postponed",
        "skipped": f"{name}: not looked up (the alert does not name the employer)",
    }.get(status, f"{name}: {status}{how}")


def _emitter(ctx: RunContext) -> Callable[..., None]:
    def emit(kind: str, detail: str | None = None, **data: Any) -> None:
        job_id = data.pop("job_id", None)
        job = ctx.repo.get_job(job_id) if job_id else None
        ctx.emit(kind, job, detail, **data)
    return emit


def _process_group(ctx, group: Group, report: EmployerReport, summary: DiscoverySummary, profiles, emp: EmployerRepo,  # noqa: ANN001
                   gateway: SearchGateway, reconciler: Reconciler, fetch, meter: PageMeter, run_budget: RunBudget,
                   emit, *, again: bool, refresh_site: bool, website: str | None, now) -> None:  # noqa: ANN001
    cfg = ctx.config.sources.employer_careers
    resolver = IdentityResolver(emp, cfg, fetch, meter, gateway, emit, now=now)
    ident = resolver.confirm(group, website) if website else resolver.resolve(group, again=again, refresh_site=refresh_site)
    report.identity, report.employer_id, report.source = ident.status, ident.employer_id, ident.source
    report.searches, report.reason, report.kind = ident.searches, ident.reason, ident.kind
    why = f": {ident.reason}" if ident.status in ("unresolved", "ambiguous", "deferred") and ident.reason else ""
    emit("employer_resolved", resolved_text(group.name, ident.status, ident.source) + why,
         status=ident.status, source=ident.source, searches=ident.searches, reason_kind=ident.kind)
    if ident.status != "verified":
        for seed in group.seeds:
            if ident.status == "deferred":
                summary.deferred_jobs[seed.job_id] = ident.reason or "deferred"
            elif ident.status in ("ambiguous", "unresolved"):
                emp.record_seed(seed.job_id, f"employer_{ident.status}", employer_id=ident.employer_id,
                                reason=ident.reason)
        if ident.status == "deferred":
            emit("employer_deferred", f"{group.name}: {ident.reason}", reason_kind=ident.kind, jobs=len(group.seeds))
        return
    employer = emp.get(ident.employer_id)
    wanted = wanted_priority(group.titles, _profiles_for(group, profiles))
    scanner = EmployerScanner(emp, cfg, fetch, run_budget, meter, emit, now=now, run_id=ctx.run_id)
    scan = scanner.scan(employer, again=again, want=wanted)
    report.scan = scan
    summary.pages += scan.pages
    if scan.status == "failed" and not scan.listings:
        emit("warning", f"{group.name}: careers site not read ({scan.reason})")
        for seed in group.seeds:
            emp.record_seed(seed.job_id, "scan_failed", employer_id=employer["id"], reason=scan.reason)
        return
    listings = list(scan.listings)
    partial = scan.status == "partial"
    attached: set[str] = set()
    for seed in group.seeds:
        job = ctx.repo.get_job(seed.job_id)
        if job is None:
            continue
        listings = _ensure_detail(scanner, emp, employer, job, listings)
        outcome = reconciler.reconcile_seed(employer, job, listings, scan_partial=partial)
        if outcome.outcome == "matched":
            report.matched.append(job["id"])
            _recheck_form(ctx, job["id"], emit)
            if outcome.posting_url:
                attached.add(outcome.posting_url)
            if outcome.recovered:
                report.recovered.append(job["id"])
                _refilter(ctx, job["id"], profiles)
        elif outcome.outcome == "unresolved" or outcome.outcome == "conflict":
            report.unresolved.append(job["id"])
        else:
            report.not_found.append(job["id"])
    related = reconciler.import_related(employer, listings, _profiles_for(group, profiles), attached_urls=attached,
                                        seed_job_ids=report.seeds)
    report.related_created, report.related_linked = related.created, related.linked


@dataclass
class OwnAdsSummary:
    searched: int = 0
    found: list[str] = field(default_factory=list)
    search_requests: int = 0

    def lines(self) -> list[str]:
        if not self.searched:
            return []
        return [f"own ads: {len(self.found)} of {self.searched} LinkedIn/StepStone/Indeed/Arbeitsagentur jobs found on "
                f"the employer's site by searching company and title ({self.search_requests} search requests)"]


def find_own_ads(ctx: RunContext, *, job_ids: list[str] | None = None, again: bool = False, fetch: Callable | None = None,
                 api: ParallelSearch | None = None, now: Callable[[], datetime] | None = None) -> OwnAdsSummary:
    """For every alert job the employer's careers site did not give: search "<company> <job title>" for its own ad.

    Runs after the job pages are read, so a result is compared with the ad's text: two ads with the same title
    (an Angular and a Java "Software Engineer") are told apart.
    """
    cfg = ctx.config.sources.employer_careers
    summary = OwnAdsSummary()
    # The Arbeitsagentur rule always applies; platform alerts only with employer discovery switched on.
    sources = OWN_AD_SOURCES if cfg.enabled else ("arbeitsagentur",)
    rows = eligible_seed_rows(ctx.conn, job_ids=job_ids, profiles=list(ctx.config.search_profiles), again=again,
                              sources=sources, statuses=OWN_AD_STATUSES)
    if not rows:
        return summary
    if api is None and not os.environ.get("PARALLEL_API_KEY"):
        ctx.emit("warning", None, f"{len(rows)} jobs on LinkedIn/StepStone/Indeed/Arbeitsagentur were not searched on "
                 "the employer's site: PARALLEL_API_KEY is not set in workspace/.env")
        return summary
    close_fetch: Callable[[], None] = lambda: None  # noqa: E731
    if fetch is None:
        fetch, close_fetch = make_fetch(ctx)
    own_api = api is None
    api = api or ParallelSearch(os.environ.get("PARALLEL_API_KEY", ""))
    gateway = SearchGateway(ctx.conn, cfg, api)
    run_budget = RunBudget(pages=cfg.max_pages_per_run, openings=cfg.max_openings_per_run)
    try:
        summary.found = _direct_postings(ctx, rows, EmployerRepo(ctx.conn), gateway, fetch, run_budget, _emitter(ctx),
                                         summary, again=again, now=now)
    finally:
        close_fetch()
        if own_api:
            api.close()
    summary.search_requests = gateway.usage.requests
    if summary.search_requests:
        ctx.emit("employer_search_usage", None, f"{summary.search_requests} search requests",
                 requests=summary.search_requests, failures=gateway.usage.failures,
                 estimated_usd=gateway.usage.estimated_usd)
    return summary


def _direct_postings(ctx, rows, emp: EmployerRepo, gateway: SearchGateway, fetch, run_budget: RunBudget,  # noqa: ANN001
                     emit, summary: OwnAdsSummary, *, again: bool, now) -> list[str]:  # noqa: ANN001
    cfg = ctx.config.sources.employer_careers
    restricted = ctx.config.assist.restricted_domains
    memo = PostingMemo(ctx.conn, cfg.failure_ttl_hours, now)
    found: list[str] = []
    for row in rows:
        seed = seed_of(row)
        job = ctx.repo.get_job(seed.job_id)
        if job is None or not (seed.title or "").strip() or is_unnamed_client(seed.company or ""):
            continue
        app = ctx.repo.get_application(job["id"])
        current = job["application_url"]
        if app is not None and app["form_status"] == "found":
            continue
        if current and not is_restricted(current, restricted) and not is_directory(current) and current != job["job_url"]:
            continue  # the alert already gave an address that is not a job board
        miss = None if again else memo.recent_miss(job["id"])
        if miss:
            emit("posting_searched", f"own ad not searched again before {cfg.failure_ttl_hours} hours pass "
                 f"(last time: {miss})", job_id=job["id"], found=False)
            continue
        meter = PageMeter(run_budget, MAX_PAGES)  # its own pages: the website check cannot use them up
        if meter.left <= 0:
            emit("posting_searched", "own ad not searched: the page limit for this run is used up", job_id=job["id"],
                 found=False)
            continue
        summary.searched += 1
        result = find_posting(gateway, meter.wrap(fetch), job_id=job["id"], company=seed.company, title=seed.title,
                              city=seed.city, description=job["description"])
        if isinstance(result, PostingMiss):
            if not result.deferred:
                memo.save(job["id"], found=False, url=None, reason=result.reason)
            emit("posting_searched", f"own ad not found by searching company and title: {result.reason}",
                 job_id=job["id"], found=False)
            continue
        memo.save(job["id"], found=True, url=result.url, reason=result.reason)
        _attach_direct(ctx, emp, job, app, result, restricted, emit)
        found.append(job["id"])
    return found


def _attach_direct(ctx, emp: EmployerRepo, job, app, hit: PostingHit, restricted, emit) -> None:  # noqa: ANN001
    repo = ctx.repo
    current = job["application_url"]
    updates: dict[str, Any] = {"application_url": hit.url}
    recovered = False
    if not (job["description"] or "").strip() and hit.description and job["status"] in ENRICHABLE:
        updates["description"], recovered = hit.description, True
    repo.update_job(job["id"], **updates)
    if recovered and job["status"] == Status.DESCRIPTION_UNAVAILABLE.value:
        repo.transition(job["id"], Status.NEW, note="description recovered from the employer's own ad")
    emp.record_seed(job["id"], "matched", posting_url=hit.url, reason=f"found by searching company and title ({hit.reason})",
                    replaced_application_url=current)
    emit("employer_matched", f"{hit.url} (found by searching \"{job['company']} {job['title']}\")", job_id=job["id"],
         recovered=recovered, direct=True)
    _recheck_form(ctx, job["id"], emit)


def _recheck_form(ctx, job_id: str, emit) -> None:  # noqa: ANN001
    """A job already prepared with the platform link (folder marked _RESTRICTED) now has its own ad: look for the
    form there at once, so the folder, its link and the "Finish forms" list are right."""
    app = ctx.repo.get_application(job_id)
    if app is None or app["channel"] == "email" or app["form_status"] == "found":
        return
    from autoapp.formfinder.store import resolve

    job = ctx.repo.get_job(job_id)
    try:
        resolve(ctx, job, again=True, refresh_package=True)
    except Exception as err:  # noqa: BLE001 - the found ad stays attached; the form is checked again later
        emit("warning", f"{job['company']}: own ad found, but its form could not be checked yet ({err})", job_id=job_id)


def _ensure_detail(scanner: EmployerScanner, emp: EmployerRepo, employer, job, listings: list[Listing]) -> list[Listing]:  # noqa: ANN001
    """Read the matched posting's page when the scan did not, so a missing description can be recovered."""
    if (job["description"] or "").strip() or job["status"] not in ("new", "description_unavailable"):
        return listings
    pre = match_seed(seed_facts_from_job(job), listings)
    if pre.outcome != "matched" or pre.listing is None or pre.listing.description:
        return listings
    detail = scanner.read_detail(employer, pre.listing)
    if detail is None:
        return listings
    emp.save_listings(employer["id"], None, [detail])
    return [detail if item.url == detail.url else item for item in listings]


def _refilter(ctx: RunContext, job_id: str, profiles: list[SearchProfile]) -> None:
    """Reapply the ordinary pre-filter with the facts the employer posting added, before evaluation."""
    job = ctx.repo.get_job(job_id)
    if job is None or job["status"] != Status.NEW.value:
        return
    relevant = [p for p in profiles if job["search_profile"] in (None, p.name)] or profiles
    candidate = Candidate(title=job["title"], company=job["company"],
                          posted_at=date.fromisoformat(job["posted_at"][:10]) if job["posted_at"] else None,
                          ad_salary_max=job["ad_salary_max"], work_mode=job["work_mode"],
                          working_time=job["working_time"], contract_type=job["contract_type"])
    reasons = [check(candidate, p, today=date.today()) for p in relevant]
    if reasons and all(reasons):
        ctx.repo.transition(job_id, Status.FILTERED, note=f"after the employer posting: {reasons[0]}")
