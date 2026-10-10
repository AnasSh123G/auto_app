"""Detail step: fetch the page of every ``new`` job without a description.

LinkedIn, Indeed and StepStone pages go through the browser fetcher (low volume, stops at
the first bot challenge); everything else uses plain HTTP. A job whose page cannot be
read becomes ``description_unavailable`` for manual handling (``autoapp describe``).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from autoapp.db.status import Status
from autoapp.fetch.browser_fetcher import BrowserFetcher, site_key
from autoapp.fetch.extract import closed_notice, extract_job
from autoapp.fetch.http_fetcher import FetchResult, HttpFetcher
from autoapp.steps.context import RunContext

PLATFORM_SITES = ("linkedin", "indeed", "stepstone")


@dataclass
class FetchSummary:
    fetched: int = 0
    unavailable: int = 0
    closed: int = 0
    blocked_sites: dict[str, str] = field(default_factory=dict)


def _source_flag(ctx: RunContext, job_id: str, flag: str) -> bool:
    for s in ctx.repo.sources(job_id):
        try:
            if json.loads(s["raw"] or "{}").get(flag):
                return True
        except (json.JSONDecodeError, AttributeError):
            continue
    return False


def _employer_posting(ctx: RunContext, job_id: str) -> str | None:
    """The verified employer posting of a matched alert job: read it instead of the platform page."""
    row = ctx.conn.execute(
        "SELECT posting_url FROM employer_seeds WHERE job_id = ? AND outcome = 'matched'", (job_id,)).fetchone()
    return row["posting_url"] if row and row["posting_url"] else None


def _where(url: str) -> str:
    """``stepstone`` / ``linkedin`` / ``indeed`` for platform pages, else the host name."""
    site = site_key(url)
    return site if site in PLATFORM_SITES else (urlsplit(url).hostname or url).removeprefix("www.")


def fetch_details(ctx: RunContext, *, http: HttpFetcher | None = None, browser: BrowserFetcher | None = None) -> FetchSummary:
    """Read the page of every new job without a description, saying what happens to each page as it goes.

    Every page gets a line: read, could not be read (and why), blocked by the site (its other pages are then
    skipped for this run) or skipped. Every pause before a page is announced with its length, and every line
    starts with the progress (``[4/34]``).
    """
    summary = FetchSummary()
    repo = ctx.repo
    jobs = [j for j in repo.jobs_by_status(Status.NEW) if not (j["description"] or "").strip()]
    if not jobs:
        return summary
    http = http or ctx.http
    own_browser = browser is None
    total = len(jobs)
    targets = [(job, _employer_posting(ctx, job["id"]) or job["job_url"]) for job in jobs]
    sites = Counter(_where(url) if site_key(url) in PLATFORM_SITES else "other sites" for _, url in targets if url)
    ctx.emit("details_started", detail=f"{total} job pages to read ("
             + ", ".join(f"{name} {n}" for name, n in sorted(sites.items(), key=lambda kv: (-kv[1], kv[0]))) + ")", total=total)
    position = {"i": 0, "job": None}

    def on_wait(where: str, seconds: float) -> None:
        if seconds < 1:
            return
        until = datetime.now(UTC) + timedelta(seconds=seconds)
        ctx.emit("detail_waiting", position["job"],
                 detail=f"[{position['i']}/{total}] waiting {seconds:.0f} s before reading this {where} page "
                        "(a pause between pages, so the site is not overloaded)",
                 seconds=round(seconds, 1), until=until.isoformat(timespec="seconds"))

    def done(kind: str, job, text: str) -> None:  # noqa: ANN001
        left = total - position["i"]
        ctx.emit(kind, job, detail=f"[{position['i']}/{total}] {text}" + (f", {left} left" if left else ", the last one"))

    previous_http_wait = getattr(http, "on_wait", None)
    http.on_wait = on_wait
    try:
        for i, (job, url) in enumerate(targets, start=1):
            position.update(i=i, job=job)
            if not url:
                repo.transition(job["id"], Status.DESCRIPTION_UNAVAILABLE, note="no job URL")
                summary.unavailable += 1
                done("detail_skipped", job, "skipped: the job has no address")
                continue
            where = _where(url)
            if site_key(url) in PLATFORM_SITES:
                if browser is None:
                    browser = BrowserFetcher.from_config(ctx.config.detail_fetch, ctx.ws.browser_profile_dir)
                browser.on_wait = on_wait
                result: FetchResult = browser.fetch(url)
            else:
                result = http.fetch(url)
            notice = closed_notice(result.html) if result.ok and result.html else None
            if notice:
                # The ad itself says applications are no longer taken: never evaluated or prepared.
                repo.transition(job["id"], Status.EXPIRED, note=f"ad closed: {notice}")
                summary.closed += 1
                done("detail_closed", job, f"the {where} page says \"{notice}\": marked expired, not evaluated")
                continue
            data = extract_job(result.html, result.final_url or url) if result.ok and result.html else None
            if data is None or not (data.description or "").strip():
                reason = result.reason or (f"HTTP {result.status}" if result.status else "no description found")
                repo.transition(job["id"], Status.DESCRIPTION_UNAVAILABLE, note=reason)
                summary.unavailable += 1
                if "disabled" in reason or "cap reached" in reason:
                    done("detail_skipped", job, f"skipped {where}: {reason}")
                elif result.blocked:
                    done("detail_blocked", job, f"{where} blocked the app ({reason}); "
                                                f"its other pages are skipped in this run")
                elif result.ok:
                    done("detail_failed", job, f"{where} page loaded, but no job description was found in it")
                else:
                    done("detail_failed", job, f"could not read the {where} page: {reason}")
                continue
            updates = {"description": data.description}
            if data.title and _source_flag(ctx, job["id"], "title_truncated"):
                updates["title"] = data.title
            if data.company and _source_flag(ctx, job["id"], "company_unknown"):
                updates["company"] = data.company
            for key in ("application_url", "work_mode", "working_time", "contract_type"):
                if getattr(data, key) and not job[key]:
                    updates[key] = getattr(data, key)
            if data.ad_salary_min is not None and job["ad_salary_min"] is None:
                updates["ad_salary_min"] = data.ad_salary_min
                updates["ad_salary_max"] = data.ad_salary_max
            repo.update_job(job["id"], **updates)
            summary.fetched += 1
            done("detail_read", job, f"read the {where} page ({len(data.description):,} characters of description)")
    finally:
        http.on_wait = previous_http_wait
        if browser is not None:
            browser.on_wait = None
            summary.blocked_sites.update(browser.blocked_sites)
            if own_browser:
                browser.close()
        summary.blocked_sites.update(http.disabled_hosts)
    blocked = f", blocked: {', '.join(summary.blocked_sites)}" if summary.blocked_sites else ""
    closed = f", {summary.closed} ads closed" if summary.closed else ""
    ctx.emit("details_done", detail=f"{summary.fetched} job pages read, {summary.unavailable} unavailable{closed}{blocked}",
             fetched=summary.fetched, unavailable=summary.unavailable)
    return summary
