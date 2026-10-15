"""Run events: what the steps report while they work (run-dashboard spec, design decision 18).

Steps call ``ctx.emit(...)``; an ``EventBus`` hands every ``RunEvent`` to its subscribers
(database writer, log writer, live view or plain printer). ``RunStats`` turns events into the
counters at the top of the live view and in ``runs.progress``.

A subscriber that raises is logged once and removed; emitting never raises, so a broken view
or writer cannot change what a run does.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("autoapp")

# Shown in the feed in full: every email and form saved or sent.
FEED_KINDS = frozenset({"folder_saved", "email_sent", "email_would_send", "email_held", "email_failed",
                        "form_filled", "form_resolved", "draft_saved", "draft_would_save", "test_sent", "test_would_send"})
# Shown in the feed in a lower-key style.
QUIET_KINDS = frozenset({"job_approved", "job_maybe", "job_skipped", "needs_review", "source_done",
                         "details_done", "warning", "error", "draft_exists", "employer_resolved", "employer_scanned",
                         "employer_matched", "employer_unresolved", "employer_job_added", "employer_deferred",
                         "folder_renamed", "details_started", "detail_waiting", "detail_read", "detail_failed",
                         "detail_blocked", "detail_skipped", "detail_closed", "apply_link", "posting_searched",
                         "email_read", "email_job", "judge_reviewed", "judge_failed", "judge_summary"})
# Counted, never shown as feed lines.
HIDDEN_KINDS = frozenset({"step_started", "step_finished", "llm_call", "run_finished", "employer_scan_started",
                          "employer_search_usage", "employer_discovery_done"})

LABELS = {
    "judge_reviewed": "JUDGE",
    "judge_failed": "JUDGE UNAVAILABLE",
    "judge_summary": "JUDGE SCORE",
    "email_sent": "EMAIL SENT",
    "email_would_send": "WOULD SEND",
    "email_held": "EMAIL HELD",
    "email_failed": "EMAIL FAILED",
    "email_read": "EMAIL READ",
    "email_job": "EMAIL JOB",
    "folder_saved": "FOLDER SAVED",
    "form_filled": "FORM FILLED",
    "form_resolved": "FORM CHECK",
    "draft_saved": "DRAFT SAVED",
    "draft_would_save": "WOULD DRAFT",
    "draft_exists": "DRAFT EXISTS",
    "test_sent": "TEST SENT",
    "test_would_send": "WOULD TEST",
    "job_approved": "APPROVED",
    "job_maybe": "MAYBE",
    "job_skipped": "SKIPPED",
    "needs_review": "NEEDS REVIEW",
    "source_done": "SEARCH",
    "details_done": "DETAILS",
    "details_started": "PAGES TO READ",
    "detail_waiting": "PAGE WAIT",
    "detail_read": "PAGE READ",
    "detail_failed": "PAGE FAILED",
    "detail_blocked": "PAGE BLOCKED",
    "detail_skipped": "PAGE SKIPPED",
    "detail_closed": "AD CLOSED",
    "apply_link": "APPLY LINK",
    "employer_resolved": "EMPLOYER SITE",
    "employer_scanned": "EMPLOYER SCAN",
    "employer_matched": "JOB FOUND",
    "posting_searched": "AD SEARCH",
    "employer_unresolved": "JOB NOT FOUND",
    "employer_job_added": "NEW JOB",
    "employer_deferred": "LOOKUP DEFERRED",
    "folder_renamed": "FOLDER RENAMED",
    "warning": "WARNING",
    "error": "ERROR",
}

LABEL_WIDTH = max(len(label) for label in LABELS.values())  # the feed pads every tag to this width

_ARROW_KINDS = ("email_sent", "email_would_send", "draft_saved", "draft_would_save", "test_sent", "test_would_send")
_DECISION_COUNTER = {"job_approved": "approved", "job_maybe": "maybe", "job_skipped": "skipped"}


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RunEvent:
    kind: str
    job_id: str | None = None
    company: str | None = None
    title: str | None = None
    detail: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=_now)

    @property
    def level(self) -> str:
        if self.kind in FEED_KINDS:
            return "feed"
        if self.kind in QUIET_KINDS:
            return "quiet"
        return "hidden"


def format_event(e: RunEvent, *, local_time: bool = True) -> str:
    """One plain line, the same in the feed, the plain output and the log."""
    at = e.at.astimezone() if local_time else e.at
    who = ", ".join(p for p in (e.company, e.title) if p)
    parts = [at.strftime("%H:%M:%S"), LABELS.get(e.kind, e.kind.upper())]
    if who:
        parts.append(who)
    if e.detail:
        parts.append(("→ " if e.kind in _ARROW_KINDS else "") + e.detail)
    return "  ".join(parts)


@dataclass
class RunStats:
    """Counters for one run, built only from events (so a viewer can rebuild them)."""

    run_id: str = ""
    dry_run: bool = False
    mode: str | None = None
    started_at: str = ""
    step: str | None = None
    steps_done: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    daily_cap: int | None = None
    sent_today_before: int = 0
    ads_found: int = 0
    ads_new: int = 0
    duplicates: int = 0
    filtered: int = 0
    details_fetched: int = 0
    details_unavailable: int = 0
    details_total: int = 0  # job pages the fetch step has to read
    details_processed: int = 0  # of those, read or given up so far
    details_wait_until: str | None = None  # the fetch step pauses until then (ISO time) before the next page
    evaluated: int = 0
    judge_reviewed: int = 0
    judge_score_total: int = 0
    judge_disagreed: int = 0
    judge_failed: int = 0
    approved: int = 0
    maybe: int = 0
    skipped: int = 0
    folders_saved: int = 0
    email_folders: int = 0
    forms_saved: int = 0
    emails_sent: int = 0
    emails_would_send: int = 0
    emails_held: int = 0
    emails_failed: int = 0
    alert_emails_read: int = 0
    alert_email_jobs: int = 0
    forms_filled: int = 0
    form_results: dict[str, str] = field(default_factory=dict)
    drafts_saved: int = 0
    drafts_would_save: int = 0
    test_emails: int = 0
    needs_review: int = 0
    employers_seen: int = 0
    employers_cached: int = 0
    employers_deferred: int = 0
    employer_scans: int = 0
    employer_alerts_matched: int = 0
    employer_alerts_recovered: int = 0
    employer_jobs_added: int = 0
    employer_searches: int = 0
    employer_search_failures: int = 0
    employer_search_usd: float = 0.0
    warnings: int = 0
    errors: int = 0
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    cost_unknown: bool = False
    # task (evaluate, write, form_fill, form_find) -> calls, input (incl. cached), cached, output tokens, cost (None: no price)
    task_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    finished: bool = False
    ok: bool | None = None

    @property
    def sent_today(self) -> int:
        return self.sent_today_before + self.emails_sent

    @property
    def approval_share(self) -> float | None:
        return self.approved / self.evaluated if self.evaluated else None

    @property
    def judge_score(self) -> float | None:
        return self.judge_score_total / self.judge_reviewed if self.judge_reviewed else None

    @property
    def form_outcomes(self) -> dict[str, int]:
        from collections import Counter

        return dict(Counter(self.form_results.values()))

    def apply(self, e: RunEvent) -> None:
        d = e.data
        k = e.kind
        if k == "step_started":
            self.step = e.detail
        elif k == "step_finished":
            if e.detail and e.detail not in self.steps_done:
                self.steps_done.append(e.detail)
        elif k == "source_done":
            self.ads_found += int(d.get("found", 0))
            self.ads_new += int(d.get("new", 0))
            self.duplicates += int(d.get("duplicates", 0))
            self.filtered += int(d.get("filtered", 0))
        elif k == "details_started":
            self.details_total += int(d.get("total", 0))
        elif k == "detail_waiting":
            self.details_wait_until = d.get("until")
        elif k in ("detail_read", "detail_failed", "detail_blocked", "detail_skipped", "detail_closed"):
            self.details_processed += 1
            self.details_wait_until = None
            if k == "detail_read":
                self.details_fetched += 1
            else:
                self.details_unavailable += 1
        elif k == "details_done":
            self.details_wait_until = None
            if not self.details_processed:  # counted page by page already otherwise
                self.details_fetched += int(d.get("fetched", 0))
                self.details_unavailable += int(d.get("unavailable", 0))
        elif k in _DECISION_COUNTER:
            self.evaluated += 1
            name = _DECISION_COUNTER[k]
            setattr(self, name, getattr(self, name) + 1)
        elif k == "judge_reviewed":
            self.judge_reviewed += 1
            self.judge_score_total += int(d["score"])
            self.judge_disagreed += int(not d.get("agrees", True))
        elif k == "judge_failed":
            self.judge_failed += 1
        elif k == "folder_saved":
            self.folders_saved += 1
            if d.get("channel") == "email":
                self.email_folders += 1
            else:
                self.forms_saved += 1
        elif k == "email_sent":
            self.emails_sent += 1
        elif k == "email_would_send":
            self.emails_would_send += 1
        elif k == "email_held":
            self.emails_held += 1
        elif k == "email_failed":
            self.emails_failed += 1
        elif k == "email_read":
            self.alert_emails_read += 1
        elif k == "email_job":
            self.alert_email_jobs += 1
        elif k == "form_filled":
            self.forms_filled += 1
        elif k == "form_resolved" and e.job_id:
            self.form_results[e.job_id] = d["form_status"]
        elif k == "draft_saved":
            self.drafts_saved += 1
        elif k == "draft_would_save":
            self.drafts_would_save += 1
        elif k in ("test_sent", "test_would_send"):
            self.test_emails += 1
        elif k == "needs_review":
            self.needs_review += 1
        elif k == "employer_resolved":
            self.employers_seen += 1
            self.employers_cached += 1 if d.get("source") == "cache" else 0
        elif k == "employer_deferred":
            self.employers_deferred += 1
        elif k == "employer_scanned":
            self.employer_scans += 1
        elif k == "employer_matched":
            self.employer_alerts_matched += 1
            self.employer_alerts_recovered += 1 if d.get("recovered") else 0
        elif k == "employer_job_added":
            self.employer_jobs_added += 1
        elif k == "employer_search_usage":
            self.employer_searches += int(d.get("requests", 0))
            self.employer_search_failures += int(d.get("failures", 0))
            self.employer_search_usd += float(d.get("estimated_usd", 0.0))
        elif k == "warning":
            self.warnings += 1
        elif k == "error":
            self.errors += 1
        elif k == "llm_call":
            self.llm_calls += 1
            self.tokens += int(d.get("tokens", 0))
            if d.get("task"):
                row = self.task_usage.setdefault(str(d["task"]), {
                    "calls": 0, "input": 0, "cached": 0, "output": 0, "cost": 0.0})
                row["calls"] += 1
                row["input"] += int(d.get("input_tokens", 0))
                row["cached"] += int(d.get("cached_tokens", 0))
                row["output"] += int(d.get("output_tokens", 0))
                row["cost"] = None if d.get("cost_usd") is None or row["cost"] is None else row["cost"] + float(d["cost_usd"])
            if d.get("cost_usd") is None:
                self.cost_unknown = True
            else:
                self.cost_usd += float(d["cost_usd"])
        elif k == "run_finished":
            self.finished = True
            self.step = None
            self.ok = bool(d.get("ok", True))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunStats:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


Subscriber = Callable[[RunEvent], None]


class EventBus:
    """Delivers events to subscribers; a failing subscriber is logged and dropped."""

    def __init__(self) -> None:
        self._subs: list[tuple[Subscriber, Callable[[BaseException], None] | None]] = []
        # Masks secrets in the text of every event before any subscriber sees it.
        self.redact: Callable[[str], str] | None = None

    def subscribe(self, fn: Subscriber, *, on_error: Callable[[BaseException], None] | None = None) -> None:
        self._subs.append((fn, on_error))

    def unsubscribe(self, fn: Subscriber) -> None:
        self._subs = [(f, h) for f, h in self._subs if f is not fn]

    @property
    def active(self) -> bool:
        return bool(self._subs)

    def emit(self, event: RunEvent) -> None:
        if self.redact is not None:
            r = self.redact
            event = replace(event, company=r(event.company) if event.company else event.company,
                            title=r(event.title) if event.title else event.title,
                            detail=r(event.detail) if event.detail else event.detail)
        for fn, on_error in list(self._subs):
            try:
                fn(event)
            except Exception as err:  # noqa: BLE001 - a subscriber must never affect the run
                log.warning("event subscriber %r failed and was removed: %s", fn, err, exc_info=True)
                self.unsubscribe(fn)
                if on_error is not None:
                    try:
                        on_error(err)
                    except Exception:  # noqa: BLE001
                        log.warning("fallback after a subscriber failure failed", exc_info=True)


class StatsKeeper:
    """Subscriber that keeps ``RunStats`` up to date (subscribe it before the views)."""

    def __init__(self, stats: RunStats) -> None:
        self.stats = stats

    def __call__(self, e: RunEvent) -> None:
        self.stats.apply(e)


class DbEventWriter:
    """Stores every event in ``run_events`` and the counters in ``runs.progress`` (at most 1/s)."""

    def __init__(self, conn: sqlite3.Connection, run_id: str, stats: RunStats, *,
                 min_interval_s: float = 1.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.conn, self.run_id, self.stats = conn, run_id, stats
        self.min_interval_s, self.clock = min_interval_s, clock
        self._last: float | None = None
        self.progress_writes = 0

    def __call__(self, e: RunEvent) -> None:
        if e.kind != "llm_call":
            with self.conn:
                self.conn.execute(
                    "INSERT INTO run_events (run_id, at, kind, job_id, company, title, detail, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.run_id, e.at.isoformat(timespec="seconds"), e.kind, e.job_id, e.company, e.title,
                     e.detail, json.dumps(e.data, default=str) if e.data else None),
                )
        force = e.kind in ("step_started", "step_finished", "run_finished")
        now = self.clock()
        if force or self._last is None or now - self._last >= self.min_interval_s:
            self.write_progress()
            self._last = now

    def write_progress(self) -> None:
        payload = {"updated_at": _now().isoformat(timespec="seconds"), "stats": self.stats.to_dict()}
        with self.conn:
            self.conn.execute("UPDATE runs SET progress = ? WHERE id = ?", (json.dumps(payload), self.run_id))
        self.progress_writes += 1


class LogEventWriter:
    """Writes feed and quiet events to the run log as plain lines."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("autoapp.events")

    def __call__(self, e: RunEvent) -> None:
        if e.level != "hidden":
            self.logger.info(format_event(e))
