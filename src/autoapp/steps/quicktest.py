"""The quick test's checklist: one OK / FAILED / SKIPPED line per part of the app (run-modes spec).

The report is built from what the run's steps already summarise (``RunSummary``), so the steps know nothing about
it. A part the run never reached is SKIPPED with the reason, never silently OK.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from autoapp.assist.filler import is_restricted
from autoapp.config.models import Config
from autoapp.employers.scope import is_directory
from autoapp.formfinder.employer_link import _raw_facts

OK, WARN, FAILED, SKIPPED = "OK", "WARN", "FAILED", "SKIPPED"
_FAILURE_WORDS = ("error", "invalid", "rejected", "401", "403", "unauthorized", "not reachable")


@dataclass(frozen=True)
class FormLink:
    """One prepared form job of the quick test: where its application link ended up and how it was found."""

    job_id: str
    source: str
    url: str | None
    status: str
    note: str
    path: str  # direct search | careers page | externeURL | job page

    def text(self) -> str:
        return (f"{self.job_id[:6]}  {self.source} \u2192 {self.url or 'no link'}  {self.status}"
                + (f" ({self.note})" if self.note else "") + f" [{self.path}]")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str = ""

    def line(self) -> str:
        return f"  {self.status:<8} {self.name}" + (f": {self.reason}" if self.reason else "")


@dataclass
class QuickTestReport:
    checks: list[Check] = field(default_factory=list)
    form_links: list[Check] = field(default_factory=list)  # one per prepared form job, printed as its own section

    def add(self, name: str, status: str, reason: str = "") -> None:
        self.checks.append(Check(name, status, reason))

    @property
    def failed(self) -> bool:
        return any(c.status == FAILED for c in (*self.checks, *self.form_links))

    def lines(self) -> list[str]:
        every = (*self.checks, *self.form_links)
        counts = {s: sum(1 for c in every if c.status == s) for s in (OK, WARN, FAILED, SKIPPED)}
        section = ["form links:", *(f"  {c.status:<8} {c.name}" for c in self.form_links)] if self.form_links else []
        return ["quick test checklist:", *(c.line() for c in self.checks), *section,
                f"quick test: {counts[OK]} OK, {counts[WARN]} WARN, {counts[FAILED]} FAILED, "
                f"{counts[SKIPPED]} SKIPPED"]


def _first(items: Any) -> str:
    return str(next(iter(items), ""))[:160]


def _alerts(report: QuickTestReport, config: Config, search: Any) -> None:
    if not config.sources.alerts.enabled:
        report.add("alert mailbox", SKIPPED, "alert emails are switched off")
        return
    src = search.sources.get("alerts") if search else None
    note = (src.note or "") if src else ""
    if search is None:
        report.add("alert mailbox", SKIPPED, "search was not reached")
    elif note.startswith("skipped"):
        report.add("alert mailbox", SKIPPED, note)
    elif note.startswith("error"):
        report.add("alert mailbox", FAILED, note)
    elif not search.alert_emails:
        report.add("alert mailbox", SKIPPED, "no alert email in the folder")
        report.add("alert parsers", SKIPPED, "no alert email in the folder")
    else:
        report.add("alert mailbox", OK, f"{search.alert_emails} newest emails read, {src.found} job cards")
        for name, cards in sorted(search.alert_parsers.items()):
            if cards:
                report.add(f"alert parser {name}", OK, f"{cards} cards")
            else:
                report.add(f"alert parser {name}", FAILED, "an email from this sender gave no job card")


def _sources(report: QuickTestReport, config: Config, search: Any) -> None:
    enabled = {"arbeitsagentur": config.sources.arbeitsagentur.enabled, "adzuna": config.sources.adzuna.enabled,
               "workwise": config.sources.workwise.enabled, "arbeitnow": config.sources.arbeitnow.enabled}
    for name, on in enabled.items():
        label = f"source {name}"
        src = search.sources.get(name) if search else None
        note = (src.note or "") if src else ""
        low = note.lower()
        if not on:
            report.add(label, SKIPPED, "disabled")
        elif src is None:
            report.add(label, SKIPPED, "not reached")
        elif "skipped" in low:
            report.add(label, SKIPPED, note)
        elif any(w in low for w in _FAILURE_WORDS):
            report.add(label, FAILED, note)
        elif src.found == 0 and ("limit" in low or "cap" in low):
            report.add(label, SKIPPED, note)
        elif src.found == 0:
            report.add(label, SKIPPED, "asked, but no job came back" + (f" ({note})" if note else ""))
        else:
            report.add(label, OK, f"{src.found} jobs found, {src.new} new" + (f" ({note})" if note else ""))


def _employers(report: QuickTestReport, config: Config, summary: Any, env: Mapping[str, str]) -> None:
    needed = config.sources.alerts.mode == "search_first" or config.sources.employer_careers.enabled
    if not needed:
        report.add("search key", SKIPPED, "not needed (platform mode, employer lookup off)")
    elif env.get("PARALLEL_API_KEY"):
        report.add("search key", OK, "PARALLEL_API_KEY is set")
    else:
        report.add("search key", FAILED, "PARALLEL_API_KEY is not set")
    alerts, emp, own = summary.alerts, summary.employers, summary.own_ads
    requests = sum(x.search_requests for x in (alerts, emp, own) if x is not None)
    direct = len(alerts.direct) if alerts else 0
    saved = sum(1 for e in (emp.employers if emp else []) if e.source == "cache")
    if emp is not None and emp.errors:
        report.add("employer lookup", FAILED, _first(emp.errors))
    elif emp is not None and emp.search_requests and emp.search_failures >= emp.search_requests:
        report.add("employer lookup", FAILED, f"all {emp.search_failures} searches failed")
    elif requests or saved:
        via = len(alerts.via_careers) if alerts else 0
        report.add("employer lookup", OK, f"{requests} search requests, {saved} saved sites used, {direct} ads found "
                   f"by search, {via} on careers pages")
    else:
        waiting = {**(alerts.not_found if alerts else {}), **(alerts.deferred if alerts else {})}
        reason = _first(waiting.values()) or ("alert job without an employer name" if alerts and alerts.no_company
                                              else "no alert job to look up")
        report.add("employer lookup", SKIPPED, reason)


def _pages(report: QuickTestReport, summary: Any) -> None:
    fetch, emp = summary.fetch, summary.employers
    direct = len(summary.alerts.direct) if summary.alerts else 0
    if fetch is not None and fetch.blocked_sites:
        report.add("reading pages", FAILED, "blocked: " + ", ".join(fetch.blocked_sites))
    elif (fetch is not None and fetch.fetched) or (emp is not None and emp.pages) or direct:
        report.add("reading pages", OK, f"{fetch.fetched if fetch else 0} job pages, "
                   f"{emp.pages if emp else 0} employer pages, {direct} ads read from search results")
    else:
        report.add("reading pages", SKIPPED, "no page needed reading or none could be read")


def _model_and_letters(report: QuickTestReport, summary: Any) -> None:
    ev, prep = summary.evaluate, summary.prepare
    if ev is None:
        report.add("AI model (evaluation)", SKIPPED, "not reached")
    elif ev.evaluated:
        report.add("AI model (evaluation)", OK, f"{ev.evaluated} jobs evaluated")
    elif ev.failed or (ev.stopped and "limit" not in ev.stopped.lower()):
        report.add("AI model (evaluation)", FAILED, ev.stopped or _first(ev.errors) or f"{ev.failed} evaluations failed")
    else:
        report.add("AI model (evaluation)", SKIPPED, "no job to evaluate")
    if prep is None:
        report.add("letter writing", SKIPPED, "not reached")
        report.add("form finding", SKIPPED, "not reached")
        return
    if prep.prepared:
        report.add("letter writing", OK, f"{prep.prepared} applications prepared")
    elif prep.failed or (prep.stopped and "limit" not in prep.stopped.lower()):
        report.add("letter writing", FAILED, _first(prep.failed) or prep.stopped or "")
    else:
        report.add("letter writing", SKIPPED, "no job was prepared")
    if prep.form_outcomes:
        report.add("form finding", OK, ", ".join(f"{n} {status}" for status, n in prep.form_outcomes.items()))
    elif prep.prepared:
        report.add("form finding", SKIPPED, "no online-form application among the prepared jobs")
    else:
        report.add("form finding", SKIPPED, "nothing was prepared")


def _test_email(report: QuickTestReport, summary: Any) -> None:
    t = summary.test
    if t is None:
        report.add("test email", SKIPPED, "not reached")
    elif t.errors:
        report.add("test email", FAILED, _first(t.errors))
    elif t.sent:
        report.add("test email", OK, f"{len(t.sent)} test email sent to {t.own_address}")
    elif t.not_sent:
        report.add("test email", SKIPPED, "would not be sent: " + "; ".join(next(iter(t.not_sent.values())))[:160])
    else:
        report.add("test email", SKIPPED, "no email application was prepared")


def _resolution_path(ctx: Any, job: Any, sources: list[str]) -> str:
    """How the application link was reached: the direct company-and-title search, the careers page, the
    Arbeitsagentur's externeURL, or the job page itself."""
    seed = ctx.conn.execute("SELECT reason FROM employer_seeds WHERE job_id = ? AND outcome = 'matched'",
                            (job["id"],)).fetchone()
    if seed is not None:
        return "direct search" if (seed["reason"] or "").startswith("found by searching company and title") \
            else "careers page"
    if "arbeitsagentur" in sources and _raw_facts(ctx, job)[1]:
        return "externeURL"
    return "job page"


def form_links(ctx: Any, job_ids: list[str]) -> list[FormLink]:
    """One entry per prepared online-form job (an email application has no form link)."""
    links: list[FormLink] = []
    for job_id in job_ids:
        app = ctx.repo.get_application(job_id)
        job = ctx.repo.get_job(job_id)
        if job is None or app is None or app["channel"] == "email":
            continue
        sources = list(dict.fromkeys(s["source"] for s in ctx.repo.sources(job_id))) or ["unknown"]
        links.append(FormLink(job_id, ", ".join(sources), job["application_url"], app["form_status"] or "unchecked",
                              " ".join((app["form_note"] or "").split())[:120], _resolution_path(ctx, job, sources)))
    return links


def _form_links(report: QuickTestReport, config: Config, links: list[FormLink]) -> None:
    restricted = config.assist.restricted_domains
    for link in links:
        url = link.url or ""
        if url and (is_directory(url) or is_restricted(url, restricted)):
            status = FAILED  # a job board is never the employer's application page
        else:
            status = OK if link.status == "found" else WARN
        report.form_links.append(Check(link.text(), status))


def build_report(config: Config, summary: Any, env: Mapping[str, str] | None = None,
                 links: list[FormLink] | None = None) -> QuickTestReport:
    """The checklist for one quick test, from the config it ran with and the run summary it produced."""
    env = os.environ if env is None else env
    report = QuickTestReport()
    _alerts(report, config, summary.search)
    _sources(report, config, summary.search)
    _employers(report, config, summary, env)
    _pages(report, summary)
    _model_and_letters(report, summary)
    _test_email(report, summary)
    _form_links(report, config, links or [])
    if summary.errors:
        report.add("run steps", FAILED, "; ".join(str(e) for e in summary.errors[:3])[:200])
    return report
