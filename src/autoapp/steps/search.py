"""Search step: query the sources, ingest with deduplication, pre-filter.

A failing source never stops the others. Arbeitsagentur details are fetched only for
new jobs that pass the pre-filter. Alert emails are read from the alert mailbox;
StepStone links are resolved to job IDs at ingestion (their tracking links expire).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from autoapp.config.models import SearchProfile
from autoapp.db.status import Status
from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.discovery.prefilter import Candidate, check
from autoapp.sources.alerts.imap_reader import AlertMailbox, ImapAlertMailbox, ingest_alerts
from autoapp.sources.models import RawJob
from autoapp.sources.stepstone_links import StepStoneLinkResolver
from autoapp.steps.context import RunContext


@dataclass
class SourceReport:
    found: int = 0
    new: int = 0
    linked: int = 0
    filtered: int = 0
    note: str | None = None  # skipped reason or error
    details: dict[str, int] = field(default_factory=dict)


@dataclass
class SearchSummary:
    sources: dict[str, SourceReport] = field(default_factory=dict)
    unrecognised_emails: int = 0
    warnings: list[str] = field(default_factory=list)
    alert_emails: int = 0  # alert emails read and parsed in this run
    alert_parsers: dict[str, int] = field(default_factory=dict)  # parser name -> cards, for the quick test

    def report(self, name: str) -> SourceReport:
        return self.sources.setdefault(name, SourceReport())


def _home_city(sp: SearchProfile | None) -> str | None:
    return sp.locations[0].name if sp and sp.locations else None


def _filter_reason(c: Candidate, profiles: list[SearchProfile], today: date) -> str | None:
    """Drop only when every relevant profile rejects the job; return the first reason."""
    reasons = [check(c, p, today=today) for p in profiles]
    return None if any(r is None for r in reasons) or not reasons else reasons[0]


def _store(ctx: RunContext, raw: RawJob, report: SourceReport, profiles: list[SearchProfile], today: date,
           detail: Callable[[RawJob], RawJob] | None = None) -> str | None:
    """Ingest one posting; return the job ID if it created a new, unfiltered job."""
    report.found += 1
    sp = ctx.search_profile(raw.search_profile)
    out = ingest(ctx.repo, raw, home_city=_home_city(sp))
    if not out.created:
        report.linked += 1
        return None
    report.new += 1
    candidate = Candidate.from_raw_job(raw)
    if raw.source == "arbeitnow":
        from autoapp.sources.arbeitnow import candidate_of

        candidate = candidate_of(raw)
    reason = _filter_reason(candidate, [sp] if sp else profiles, today)
    if reason:
        report.filtered += 1
        ctx.repo.transition(out.job_id, Status.FILTERED, note=reason)
        return None
    if detail is not None:
        try:
            ingest(ctx.repo, detail(raw), home_city=_home_city(sp))
        except Exception as err:  # noqa: BLE001 - one bad detail call must not stop the search
            report.note = f"some details failed: {type(err).__name__}"
    return out.job_id


def run_search(
    ctx: RunContext,
    profile_name: str | None = None,
    *,
    api_sources: Iterable | None = None,
    mailbox: AlertMailbox | None = None,
    resolver: StepStoneLinkResolver | None = None,
    today: date | None = None,
) -> SearchSummary:
    summary = SearchSummary()
    today = today or datetime.now(UTC).date()
    profiles = [p for p in ctx.config.search_profiles if profile_name in (None, p.name)]
    if profile_name and not profiles:
        summary.warnings.append(f"no search profile named {profile_name!r}")
        return summary

    if api_sources is None:
        api_sources = _default_sources(ctx, profiles=profiles, today=today)
    for source in api_sources:
        report = summary.report(source.name)
        skip = getattr(source, "skip_reason", lambda: None)()
        if skip:
            report.note = skip
            continue
        detail = getattr(source, "fetch_detail", None)
        try:
            _search_source(ctx, source, report, profiles, today, detail)
        except Exception as err:  # noqa: BLE001 - isolate source failures
            report.note = f"error: {err}"
        stopped = getattr(source, "stop_reason", None)
        if stopped:
            report.note = f"{source.name}: {stopped}"
        source_note = getattr(source, "report_note", None)
        if source_note:
            report.note = f"{source.name}: {source_note}"
        report.details = dict(getattr(source, "diagnostics", {}))
        _emit_source(ctx, source.name, report)

    _search_alerts(ctx, summary, profiles, today, mailbox, resolver)
    if "alerts" in summary.sources:
        _emit_source(ctx, "alerts", summary.sources["alerts"], unrecognised=summary.unrecognised_emails)
    return summary


def _search_source(ctx: RunContext, source, report: SourceReport, profiles: list[SearchProfile],  # noqa: ANN001
                   today: date, detail) -> None:  # noqa: ANN001
    """Query one source with every profile; the quick test asks only the first profile and stops at a few jobs."""
    sample = ctx.sample
    kept = 0
    for sp in profiles[:1] if sample else profiles:
        for raw in source.search(sp):
            if _store(ctx, raw, report, profiles, today, detail):
                kept += 1
            if sample and (kept >= sample.jobs_per_source or report.found >= sample.jobs_per_source * 5):
                return  # closes the source's generator: no further pages are requested


def _emit_source(ctx: RunContext, name: str, r: SourceReport, **extra: int) -> None:
    detail = f"{name}: {r.found} found, {r.new} new, {r.linked} duplicates, {r.filtered} filtered"
    if r.details:
        detail += " [" + ", ".join(f"{key}: {value}" for key, value in sorted(r.details.items())) + "]"
    ctx.emit("source_done", detail=detail + (f" ({r.note})" if r.note else ""), source=name,
             found=r.found, new=r.new, duplicates=r.linked, filtered=r.filtered, **r.details, **extra)
    if r.note and ("error" in r.note or "limit" in r.note or "429" in r.note
                   or "partial" in r.note or "invalid" in r.note or "unresolved" in r.note):
        ctx.emit("warning", detail=r.note if r.note.startswith(name) else f"{name}: {r.note}")


def _default_sources(ctx: RunContext, *, profiles: list[SearchProfile] | None = None,
                     today: date | None = None) -> list:
    from autoapp.sources.adzuna import AdzunaSource
    from autoapp.sources.arbeitnow import ArbeitnowSource
    from autoapp.sources.arbeitsagentur import ArbeitsagenturSource
    from autoapp.sources.budget import RequestBudget
    from autoapp.sources.workwise import WorkwiseSource

    out = []
    if ctx.config.sources.arbeitsagentur.enabled:
        out.append(ArbeitsagenturSource())
    cfg = ctx.config.sources.adzuna
    if cfg.enabled:
        budget = RequestBudget(ctx.conn, "adzuna", per_run=cfg.max_requests_per_run,
                               per_day=cfg.max_requests_per_day, per_month=cfg.max_requests_per_month)
        out.append(AdzunaSource(budget=budget))
    cfg_ar = ctx.config.sources.arbeitnow
    if cfg_ar.enabled:
        budget = RequestBudget(ctx.conn, "arbeitnow", per_run=cfg_ar.max_requests_per_run,
                               per_day=cfg_ar.max_requests_per_day, per_month=cfg_ar.max_requests_per_month,
                               today=today)
        out.append(ArbeitnowSource(
            profiles=profiles if profiles is not None else ctx.config.search_profiles,
            max_pages=cfg_ar.max_pages_per_run, delay_s=cfg_ar.min_delay_s, budget=budget, today=today,
        ))
    cfg_ww = ctx.config.sources.workwise
    if cfg_ww.enabled:
        budget = RequestBudget(ctx.conn, "workwise", per_run=cfg_ww.max_requests_per_run,
                               per_day=cfg_ww.max_requests_per_day, per_month=cfg_ww.max_requests_per_month,
                               today=today)
        out.append(WorkwiseSource(max_pages=cfg_ww.max_pages_per_keyword, page_size=cfg_ww.page_size,
                                  delay_s=cfg_ww.min_delay_s, budget=budget, today=today))
    return out


def _search_alerts(ctx: RunContext, summary: SearchSummary, profiles: list[SearchProfile], today: date,
                   mailbox: AlertMailbox | None, resolver: StepStoneLinkResolver | None) -> None:
    alerts = ctx.config.sources.alerts
    if not alerts.enabled:
        return
    report = summary.report("alerts")
    if mailbox is None:
        account = ctx.config.mail.alerts
        if account is None:
            report.note = "skipped (no alert mailbox configured)"
            return
        password = os.environ.get(account.password_env or "", "")
        if not password:
            report.note = f"skipped ({account.password_env} is not set)"
            return
        from autoapp.mail.connection import ssl_context_for

        mailbox = ImapAlertMailbox(account, password, ssl_context=ssl_context_for(account), since_days=30,
                                   max_messages=ctx.sample.alert_emails if ctx.sample else None)

    repo = ctx.repo

    def is_processed(mid: str) -> bool:
        return repo.conn.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (mid,)).fetchone() is not None

    def mark(mid: str, sender: str, cards: int) -> None:
        if ctx.dry_run:
            return
        with repo.conn:
            repo.conn.execute(
                "INSERT OR IGNORE INTO processed_emails (message_id, sender, processed_at, cards) VALUES (?, ?, ?, ?)",
                (mid, sender, datetime.now(UTC).isoformat(), cards),
            )

    try:
        result = ingest_alerts(mailbox, alerts.folder, is_processed, mark,
                               newest=ctx.sample.alert_emails if ctx.sample else None)
    except Exception as err:  # noqa: BLE001
        report.note = f"error: {err}"
        return
    summary.unrecognised_emails += result.unrecognised
    summary.alert_emails += len(result.processed_ids)
    summary.alert_parsers.update(result.parsers)
    summary.warnings += result.warnings
    own_resolver = resolver is None
    resolver = resolver or StepStoneLinkResolver()
    try:
        for _mid, card in result.cards:
            resolved = resolver.resolve(card.url) if card.source.startswith("stepstone") and card.url else None
            job_id = _store(ctx, card_to_raw(card, resolved), report, profiles, today)
            if job_id and resolved is not None and not resolved.ok:
                # Unresolvable card: keep the email data, mark it for manual handling.
                repo.transition(job_id, Status.DESCRIPTION_UNAVAILABLE, note=resolved.error)
    finally:
        if own_resolver:
            resolver.close()
