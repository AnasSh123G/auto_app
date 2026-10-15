"""The ``run`` command: search → fetch → (filter) → evaluate → prepare → send → export.

Each step works on the jobs the previous steps left in the matching status, so a run can
be interrupted and resumed. Steps that need email are skipped (not fatal) when email is
not configured; configuration and authentication problems are reported as errors so a
scheduled run exits non-zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autoapp.db.repo import Repo
from autoapp.documents import DocumentError
from autoapp.employers.service import (
    AlertResolutionSummary,
    DiscoverySummary,
    OwnAdsSummary,
    add_careers_matches,
    alerts_search_first,
    find_alert_ads,
    find_own_ads,
    own_ads_wanted,
    run_discovery,
    seed_arbeitsagentur_misses,
)
from autoapp.llm.cost import CostSummary, format_cost
from autoapp.steps.context import RunContext
from autoapp.steps.drafts import CheckSummary, DraftSummary, check_drafts, draft_applications
from autoapp.steps.evaluate import EvaluateSummary, evaluate_jobs
from autoapp.steps.fetch import FetchSummary, fetch_details
from autoapp.steps.prepare import PrepareSummary, prepare_jobs, select_sample_jobs
from autoapp.steps.search import SearchSummary, run_search
from autoapp.steps.send import SendSummary, TestRunSummary, send_applications, send_test_applications
from autoapp.tracking.csv_export import export_csv, sync_csv


@dataclass
class RunSummary:
    search: SearchSummary | None = None
    alerts: AlertResolutionSummary | None = None
    employers: DiscoverySummary | None = None
    own_ads: OwnAdsSummary | None = None
    late_employers: DiscoverySummary | None = None  # careers pages for the Arbeitsagentur jobs the own-ad search missed
    fetch: FetchSummary | None = None
    evaluate: EvaluateSummary | None = None
    prepare: PrepareSummary | None = None
    send: SendSummary | None = None
    drafts: DraftSummary | None = None
    drafts_check: CheckSummary | None = None
    test: TestRunSummary | None = None
    cost: CostSummary | None = None
    mode: str | None = None
    source_mode: str | None = None
    alert_mode: str | None = None  # search_first | platform
    dry_run: bool = False
    forms_to_finish: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.mode:
            labels = {"automatic": "full automatic", "review": "review first", "test": "test run"}
            out.append(f"mode: {labels.get(self.mode, self.mode)}" + (" (dry run)" if self.dry_run else ""))
        if self.source_mode:
            labels = {"email_only": "email only", "full": "full (email first, then websites)", "web_only": "web only"}
            out.append(f"job sources: {labels.get(self.source_mode, self.source_mode)}")
        if self.alert_mode:
            out.append("alert mode: " + {"search_first": "search first (employer ads found by search)",
                                         "platform": "platform (job board pages are read)"}.get(self.alert_mode, self.alert_mode))
        if self.drafts_check and (self.drafts_check.sent or self.drafts_check.missing):
            c = self.drafts_check
            out.append(f"drafts check: {len(c.sent)} sent by you" + (f", draft missing for {', '.join(c.missing)}"
                                                                        if c.missing else ""))
        if self.search:
            for name, r in self.search.sources.items():
                extra = f" ({r.note})" if r.note else ""
                if r.details:
                    extra += " [" + ", ".join(f"{key}: {value}" for key, value in sorted(r.details.items())) + "]"
                out.append(f"search {name}: {r.found} found, {r.new} new, {r.linked} duplicates, "
                           f"{r.filtered} filtered{extra}")
            if self.search.unrecognised_emails:
                out.append(f"alert emails from unknown senders: {self.search.unrecognised_emails}")
        if self.alerts:
            out += self.alerts.lines(cards=self.search.alert_cards if self.search else None)
        if self.employers:
            out += self.employers.lines()
        if self.own_ads:
            out += self.own_ads.lines()
        if self.late_employers:
            out += self.late_employers.lines()
        if self.fetch:
            out.append(f"details: {self.fetch.fetched} fetched, {self.fetch.unavailable} unavailable"
                       + (f", blocked: {', '.join(self.fetch.blocked_sites)}" if self.fetch.blocked_sites else ""))
        if self.evaluate:
            e = self.evaluate
            out.append(f"evaluate: {e.evaluated} evaluated ({e.approved} apply, {e.review} review, {e.skipped} skip), "
                       f"{e.failed} failed, {e.waiting} waiting")
            score = "not sampled yet" if e.judge_score is None else f"{e.judge_score:.1f}/100"
            out.append(f"judge: evaluator performance {score}; {e.judge_reviewed} reviews, "
                       f"{e.judge_failed} unavailable (every 20 successful evaluations)")
        if self.prepare:
            p = self.prepare
            out.append(f"prepare: {p.prepared} prepared ({p.flagged} need review), {p.waiting} waiting")
            if p.form_outcomes:
                from autoapp.formfinder.finder import LABELS

                out.append("forms: " + ", ".join(f"{n} {LABELS[status]}" for status, n in p.form_outcomes.items()))
        if self.send:
            s = self.send
            if s.would_send:
                out.append(f"send (dry run): would send {len(s.would_send)}: {', '.join(s.would_send)}")
            out.append(f"send: {len(s.sent)} sent, {len(s.not_sent)} not sent, {len(s.flagged)} flagged for review")
            if s.unconfirmed:
                out.append(f"send unconfirmed (check your Sent folder): {', '.join(s.unconfirmed)}")
        if self.drafts:
            d = self.drafts
            from autoapp.mail.drafts import imap_utf7_decode

            where = f" in your mailbox's {imap_utf7_decode(d.folder)} folder" if d.folder else ""
            if d.would_save:
                out.append(f"drafts (dry run): would save {len(d.would_save)}: {', '.join(d.would_save)}")
            out.append(f"drafts: {len(d.saved)} saved{where}, {len(d.existing)} already saved"
                       + (f"; not drafted: {', '.join(f'{j} ({r[-1]})' for j, r in d.not_drafted.items())}"
                          if d.not_drafted else ""))
            if d.review:
                out.append("drafts to check before sending: "
                           + ", ".join(f"{j} ({'; '.join(r)[:120]})" for j, r in d.review.items()))
        if self.test:
            t = self.test
            for jid, to in (t.would_send or t.sent).items():
                verb = "would send a test email" if t.would_send else "test email sent"
                out.append(f"test run: {verb} for {jid} to {t.own_address} (a real run would send to {to})")
            out.append(f"test run: {len(t.sent)} test emails sent to you, {len(t.not_sent)} would not be sent"
                       + "".join(f"\n  {jid}: {'; '.join(r)}" for jid, r in t.not_sent.items()))
        if self.forms_to_finish:
            out.append(f"forms to finish: {len(self.forms_to_finish)} ({', '.join(self.forms_to_finish)}); "
                       "use `autoapp assist <id>` or the menu's Finish forms")
        if self.cost is not None:
            per = ", ".join(f"{t} {format_cost(c)}" for t, c in sorted(self.cost.per_task.items()))
            out.append(f"estimated LLM cost: {format_cost(self.cost.total)}" + (f" ({per})" if per else ""))
        out += [f"warning: {w}" for w in self.warnings]
        out += [f"error: {e}" for e in self.errors]
        return out


def _guard(summary: RunSummary, step: str, fn, *args: Any, ctx: RunContext | None = None, **kwargs: Any):
    if ctx is not None:
        ctx.emit("step_started", detail=step)
    try:
        return fn(*args, **kwargs)
    except Exception as err:  # noqa: BLE001 - a failing step must not hide the others' results
        summary.errors.append(f"{step}: {err}")
        return None
    finally:
        if ctx is not None:
            ctx.emit("step_finished", detail=step)


def run_pipeline(ctx: RunContext, profile_name: str | None = None, **overrides: Any) -> RunSummary:
    """Run every step once. ``overrides`` let tests inject sources, mailbox, fetchers, mailer, renderer."""
    s = RunSummary(mode=ctx.mode, source_mode=ctx.source_mode, dry_run=ctx.dry_run,
                   alert_mode=ctx.config.sources.alerts.mode)
    try:
        # Validate and cache the CV/attachments before consuming alerts or spending time on employer lookups.
        _ = ctx.documents
    except DocumentError as err:
        s.errors.append(
            f"documents: {err}. Open Setup's documents section to update {ctx.ws.profile_path} "
            f"or place the declared files in {ctx.ws.documents_dir}."
        )
        return _finish_run(ctx, s)
    tracking = ctx.config.tracking
    s.warnings += _guard(s, "csv sync", sync_csv, ctx.conn, ctx.ws.csv_path, tracking.delimiter) or []

    if not ctx.dry_run and ctx.repo.jobs_by_status("drafted"):
        kw = {"store": overrides["draft_store"]} if "draft_store" in overrides else {}
        s.drafts_check = _guard(s, "drafts check", check_drafts, ctx, **kw)
        if s.drafts_check:
            s.warnings += [f"drafts check: {e}" for e in s.drafts_check.errors]
    s.search = _guard(s, "search", run_search, ctx, profile_name, ctx=ctx, api_sources=overrides.get("api_sources"),
                      mailbox=overrides.get("mailbox"), resolver=overrides.get("resolver"))
    if s.search:
        s.warnings += s.search.warnings
    if alerts_search_first(ctx.config):
        # Search first: the employer's own ad by company and title, then its careers pages, before any page is read.
        s.alerts = _guard(s, "alert ads", find_alert_ads, ctx, ctx=ctx, fetch=overrides.get("employer_fetch"),
                          api=overrides.get("employer_api"))
        if s.alerts and s.search:
            s.alerts.llm_extracted = s.search.alert_llm_extracted
    if alerts_search_first(ctx.config) or ctx.config.sources.employer_careers.enabled:
        s.employers = _guard(s, "employer sites", run_discovery, ctx, ctx=ctx, fetch=overrides.get("employer_fetch"),
                             api=overrides.get("employer_api"))
        if s.alerts and s.employers is not None:
            add_careers_matches(ctx, s.alerts)
        if s.employers:
            s.errors += [f"employer sites: {e}" for e in s.employers.errors]
            s.warnings += s.employers.warnings
    s.fetch = _guard(s, "fetch", fetch_details, ctx, ctx=ctx, http=overrides.get("http"), browser=overrides.get("browser"))

    s.evaluate = _guard(s, "evaluate", evaluate_jobs, ctx, ctx=ctx)
    if s.evaluate and s.evaluate.stopped and "limit" not in s.evaluate.stopped.lower():
        s.errors.append(f"evaluate: {s.evaluate.stopped}")
    elif s.evaluate and s.evaluate.stopped:
        s.warnings.append(s.evaluate.stopped)

    if ctx.sample is not None and ctx.sample.ignore_threshold:
        # The quick test chooses its preparation sample (below-threshold jobs included) now, so that these jobs get the
        # own-ad search and the careers seeding below like in a real run; prepare takes exactly this sample.
        _guard(s, "sample", select_sample_jobs, ctx)

    if own_ads_wanted(ctx.config):
        # The jobs worth applying to are known now, with their ads' texts: search "<company> <job title>" for those
        # still on a job board or the Arbeitsagentur, before their folders and forms are prepared.
        s.own_ads = _guard(s, "own ads", find_own_ads, ctx, ctx=ctx, fetch=overrides.get("employer_fetch"),
                           api=overrides.get("employer_api"))
        # What that search missed on the Arbeitsagentur goes to the employer's careers pages, like the alert jobs.
        if s.own_ads and s.own_ads.missed:
            s.late_employers = _guard(s, "employer sites (Arbeitsagentur)", seed_arbeitsagentur_misses, ctx, s.own_ads,
                                      ctx=ctx, fetch=overrides.get("employer_fetch"), api=overrides.get("employer_api"))
        if s.late_employers:
            s.errors += [f"employer sites: {e}" for e in s.late_employers.errors]
            s.warnings += s.late_employers.warnings

    prepare_kwargs = {"renderer_factory": overrides["renderer_factory"]} if "renderer_factory" in overrides else {}
    s.prepare = _guard(s, "prepare", prepare_jobs, ctx, ctx=ctx, **prepare_kwargs)
    if s.prepare:
        s.warnings += s.prepare.failed
        if s.prepare.stopped and "limit" not in s.prepare.stopped.lower():
            s.errors.append(f"prepare: {s.prepare.stopped}")
        elif s.prepare.stopped and s.prepare.stopped not in s.warnings:
            s.warnings.append(s.prepare.stopped)
        for jid in s.prepare.prepared_ids:
            app = ctx.repo.get_application(jid)
            if app is not None and app["channel"] != "email":
                s.forms_to_finish.append(jid)

    if ctx.mode == "automatic":
        kw = {"renderer_factory": overrides["renderer_factory"]} if "renderer_factory" in overrides else {}
        s.send = _guard(s, "send", send_applications, ctx, ctx=ctx, mailer=overrides.get("mailer"), **kw)
        if s.send:
            s.errors += [f"send: {e}" for e in s.send.errors if s.send.auth_failed or "not set" in e
                         or "no sending mailbox" in e]
            s.warnings += [f"send: {e}" for e in s.send.errors if f"send: {e}" not in s.errors]
            s.warnings += s.send.warnings
    elif ctx.mode == "review":
        kw = {"store": overrides["draft_store"]} if "draft_store" in overrides else {}
        if "renderer_factory" in overrides:
            kw["renderer_factory"] = overrides["renderer_factory"]
        s.drafts = _guard(s, "draft", draft_applications, ctx, ctx=ctx, **kw)
        if s.drafts:
            fatal = s.drafts.auth_failed or any("not set" in e or "no sending mailbox" in e or "drafts_folder" in e
                                                for e in s.drafts.errors)
            (s.errors if fatal else s.warnings).extend(f"draft: {e}" for e in s.drafts.errors)
    elif ctx.mode == "test":
        kw = {"renderer_factory": overrides["renderer_factory"]} if "renderer_factory" in overrides else {}
        s.test = _guard(s, "test send", send_test_applications, ctx, ctx=ctx, mailer=overrides.get("mailer"), **kw)
        if s.test:
            fatal = s.test.auth_failed or any("not set" in e or "no sending mailbox" in e for e in s.test.errors)
            (s.errors if fatal else s.warnings).extend(f"test send: {e}" for e in s.test.errors)

    _guard(s, "export", export_csv, ctx.conn, ctx.ws.csv_path, tracking.delimiter,
           include_skipped=tracking.include_skipped)
    return _finish_run(ctx, s)


def _finish_run(ctx: RunContext, s: RunSummary) -> RunSummary:
    """Record the same completion summary for an early setup failure and a completed pipeline."""
    s.cost = ctx.prices.summarize(ctx.meter.usages)
    if ctx.meter.unpriced and ctx.meter.max_cost is not None:
        s.warnings.append(f"no price known for {', '.join(sorted(ctx.meter.unpriced))}: its calls are not counted "
                          f"against the ${ctx.meter.max_cost:.2f} cost limit (add the price under llm.prices)")
    for w in s.warnings:
        ctx.emit("warning", detail=str(w)[:200])
    for e in s.errors:
        ctx.emit("error", detail=str(e)[:200])
    ctx.emit("run_finished", ok=s.ok)
    Repo(ctx.conn).finish_run(ctx.run_id, {"lines": s.lines(), "ok": s.ok})
    return s
