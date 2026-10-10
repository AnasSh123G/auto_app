"""Everything a pipeline step needs, built once per command run."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from typing import Any

from autoapp.config.models import Config, Profile, SearchProfile, load_config, load_profile
from autoapp.config.workspace import Workspace
from autoapp.db.repo import DbUsageRecorder, Repo
from autoapp.db.schema import open_db
from autoapp.documents import DocumentSet, load_documents
from autoapp.events import EventBus, RunEvent, RunStats
from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.llm.base import LLMClient, Usage, UsageMeter
from autoapp.llm.cost import PriceTable


@dataclass(frozen=True)
class SampleLimits:
    """The quick test (``autoapp test``): a small sample of every step instead of a whole run (run-modes spec).

    ``RunContext.sample`` is None in every normal run, so steps only ever look at it where they already limit work.
    """

    alert_emails: int = 2  # the newest alert emails read
    jobs_per_source: int = 2  # jobs kept from each search source
    employer_lookups: int = 2  # alert jobs / employers looked up online
    evaluations: int = 4
    preparations: int = 2
    test_emails: int = 1
    arbeitnow_pages: int = 3  # Arbeitnow is read page by page and matched locally: a few pages are enough to try it
    ignore_threshold: bool = True  # prepare (and test-mail) the best evaluated jobs whatever their score

    def apply_to(self, config: Config) -> None:
        """Lower the per-run limits of ``config`` (the in-memory copy of this run) to this sample."""
        limits = config.limits
        limits.max_evaluate_per_run = min(limits.max_evaluate_per_run, self.evaluations)
        limits.max_prepare_per_run = min(limits.max_prepare_per_run, self.preparations)
        employers = config.sources.employer_careers
        employers.max_employers_per_run = min(employers.max_employers_per_run, self.employer_lookups)
        arbeitnow = config.sources.arbeitnow
        arbeitnow.max_pages_per_run = min(arbeitnow.max_pages_per_run, self.arbeitnow_pages)
        arbeitnow.max_requests_per_run = min(arbeitnow.max_requests_per_run, self.arbeitnow_pages)


@dataclass
class RunContext:
    ws: Workspace
    config: Config
    profile: Profile
    conn: sqlite3.Connection
    run_id: str
    dry_run: bool = False
    interactive: bool = True
    mode: str = "review"  # automatic | review | test (run-modes spec)
    clients: dict[str, LLMClient] = field(default_factory=dict)
    events: EventBus = field(default_factory=EventBus)
    stats: RunStats | None = None
    _meter: UsageMeter | None = None
    _http: HttpFetcher | None = None
    _apply_links: Any = None  # fetch.apply_links.RunLookup, made on first use
    # Search-first alert handling: why an alert job still has no employer ad (job id -> reason), for the detail step.
    alert_notes: dict[str, str] = field(default_factory=dict)
    sample: SampleLimits | None = None  # set only by the quick test

    @classmethod
    def open(cls, ws: Workspace, *, dry_run: bool = False, interactive: bool = True,
             start_run: bool = True, mode: str | None = None) -> RunContext:
        """Load config and profile (raises ``ConfigError``) and open the database."""
        ws.ensure_layout()
        config = load_config(ws)
        profile = load_profile(ws)
        conn = open_db(ws.db_path)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        if start_run:
            Repo(conn).start_run(run_id)
        return cls(ws=ws, config=config, profile=profile, conn=conn, run_id=run_id,
                   dry_run=dry_run, interactive=interactive, mode=mode or config.run.default_mode)

    @cached_property
    def repo(self) -> Repo:
        return Repo(self.conn)

    @property
    def http(self) -> HttpFetcher:
        if self._http is None:
            self._http = HttpFetcher()
        return self._http

    @property
    def apply_links(self):  # noqa: ANN201 - fetch.apply_links.RunLookup (imported lazily: it needs Playwright)
        """Reading apply addresses from platform pages with your login (``sources.apply_links_with_login``)."""
        if self._apply_links is None:
            from autoapp.fetch.apply_links import RunLookup

            self._apply_links = RunLookup(self)
        return self._apply_links

    @property
    def meter(self) -> UsageMeter:
        if self._meter is None:
            self._meter = UsageMeter(
                self.config.limits.max_llm_calls_per_run, _EventingRecorder(self, DbUsageRecorder(self.conn, self.run_id)),
                max_cost=self.config.limits.max_cost_per_run_usd, cost_of=self.prices.cost_of,
            )
        return self._meter

    def emit(self, kind: str, job: Any = None, detail: str | None = None, **data: Any) -> None:
        """Report a run event; ``job`` is a jobs row (or None). Never raises."""
        if not self.events.active:
            return
        self.events.emit(RunEvent(
            kind=kind,
            job_id=job["id"] if job is not None else None,
            company=job["company"] if job is not None else None,
            title=job["title"] if job is not None else None,
            detail=detail,
            data=data,
        ))

    @cached_property
    def documents(self) -> DocumentSet:
        return load_documents(self.ws, self.profile)

    @cached_property
    def prices(self) -> PriceTable:
        return PriceTable.for_workspace(self.ws, self.config.llm.prices)

    @cached_property
    def writing_instructions(self) -> str:
        p = self.ws.writing_instructions_path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def llm(self, task: str) -> LLMClient:
        if task not in self.clients:
            from autoapp.llm.factory import build_client

            self.clients[task] = build_client(self.config.llm, task)
        return self.clients[task]

    def search_profile(self, name: str | None) -> SearchProfile | None:
        return next((p for p in self.config.search_profiles if p.name == name), None)

    def close(self) -> None:
        if self._apply_links is not None:
            self._apply_links.close()
        if self._http is not None:
            self._http.close()
        self.conn.close()


class _EventingRecorder:
    """Records usage as before and reports each call as an ``llm_call`` event with its cost."""

    def __init__(self, ctx: RunContext, inner: DbUsageRecorder) -> None:
        self.ctx, self.inner = ctx, inner

    def record(self, usage: Usage) -> None:
        self.inner.record(usage)
        if self.ctx.events.active:
            try:
                cost = self.ctx.prices.summarize([usage]).total
            except Exception:  # noqa: BLE001 - a price problem must not stop the call
                cost = None
            self.ctx.emit("llm_call", tokens=usage.input_tokens + usage.output_tokens, cost_usd=cost, task=usage.task,
                          provider=usage.provider, model=usage.model, input_tokens=usage.input_tokens,
                          cached_tokens=usage.cached_input_tokens, output_tokens=usage.output_tokens)


def profile_prompt_text(profile: Profile) -> str:
    """Deterministic, privacy-minimised profile text for prompts.

    Only what judging fit and writing need: no street, phone or email.
    """
    p = profile.personal
    lines = [f"name: {p.first_name} {p.last_name}", f"city: {p.city}"]
    if profile.work_authorization:
        lines.append(f"work_authorization: {profile.work_authorization}")
    a = profile.availability
    if getattr(a, "earliest_start", None):
        lines.append(f"earliest_start: {a.earliest_start}")
    if getattr(a, "notice_period", None):
        lines.append(f"notice_period: {a.notice_period}")
    if profile.language_levels:
        lines.append("language_levels:")
        lines += [f"  {k}: {v}" for k, v in sorted(profile.language_levels.items())]
    if profile.extra_skills:
        lines.append("extra_skills (not in the CV, but real):")
        for s in profile.extra_skills:
            ctx = f" ({s.context})" if s.context else ""
            lines.append(f"  - {s.name}: {s.level}{ctx}")
    d = profile.dealbreakers
    deal = []
    if d.max_german_level_required:
        deal.append(f"jobs requiring German above {d.max_german_level_required}")
    if d.no_relocation:
        deal.append("relocation")
    if d.excluded_industries:
        deal.append("industries: " + ", ".join(d.excluded_industries))
    if deal:
        lines.append("dealbreakers: " + "; ".join(deal))
    return "\n".join(lines)


def search_summary(sp: SearchProfile | None) -> str:
    if sp is None:
        return ""
    parts = [" / ".join(sp.keywords)]
    if sp.locations:
        parts.append("in " + ", ".join(loc.name for loc in sp.locations))
    for label, value in (("work mode", sp.work_mode), ("working time", sp.working_time),
                         ("contract", sp.contract_type)):
        if value and value != "any":
            parts.append(f"{label}: {value}")
    if sp.experience_levels:
        parts.append("experience: " + ", ".join(sp.experience_levels))
    if sp.min_salary:
        parts.append(f"minimum salary {sp.min_salary} EUR")
    return "; ".join(parts)
