"""Statistics for a period, computed from the database (run-dashboard spec, task 14.3).

Periods: the latest run, today, the last 7 days, the last 30 days and all time. Past history
comes from the regular tables; numbers that only exist as run events (held-back reasons,
forms filled, unrecognised senders) come from ``run_events`` and are zero for older history.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from autoapp.config.models import Config
from autoapp.db.repo import now_iso
from autoapp.db.status import Status
from autoapp.llm.cost import CostSummary, PriceTable

BERLIN = ZoneInfo("Europe/Berlin")
PERIODS = ("run", "today", "7d", "30d", "all")
PERIOD_LABELS = {"run": "latest run", "today": "today", "7d": "last 7 days", "30d": "last 30 days", "all": "all time"}

HELD_REASONS = (
    ("daily cap", "cap"),
    ("min_score", "minimum score"),
    ("free-mail", "free-mail address"),
    ("needs_review", "needs review"),
    ("cooldown", "company cooldown"),
    ("automatic sending is off", "automatic sending off"),
)


@dataclass
class SourceStats:
    found: int = 0
    new: int = 0
    details: dict[str, int] = field(default_factory=dict)


@dataclass
class EmployerStats:
    """Employer careers discovery, separate from the job counts (employer-career-discovery spec)."""

    verified_sites: int = 0
    unverified: int = 0
    scans: dict[str, int] = field(default_factory=dict)
    alerts_matched: int = 0
    alerts_recovered: int = 0
    alerts_unresolved: int = 0
    jobs_added: int = 0
    deferred: int = 0
    searches: int = 0
    estimated_usd: float = 0.0
    search_limits: dict[str, tuple[int, int]] = field(default_factory=dict)  # label -> (used, cap)


@dataclass
class ModelUsage:
    """One model's share of the AI usage in a period; ``cost`` is None when the model has no price."""

    provider: str
    model: str
    calls: int = 0
    input_tokens: int = 0  # all input, including the cached part
    cached_tokens: int = 0
    output_tokens: int = 0
    tasks: dict[str, int] = field(default_factory=dict)  # task -> calls
    cost: float | None = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def model_usage(usages: list, prices: PriceTable) -> list[ModelUsage]:
    """Calls, tokens and estimated cost per model, busiest first."""
    by_model: dict[tuple[str, str], ModelUsage] = {}
    for u in usages:
        row = by_model.setdefault((u.provider, u.model), ModelUsage(u.provider, u.model))
        row.calls += 1
        row.input_tokens += u.input_tokens
        row.cached_tokens += min(max(u.cached_input_tokens, 0), u.input_tokens)
        row.output_tokens += u.output_tokens
        row.tasks[u.task] = row.tasks.get(u.task, 0) + 1
        cost = prices.cost_of(u)
        row.cost = None if cost is None or row.cost is None else row.cost + cost
    return sorted(by_model.values(), key=lambda r: (-r.calls, r.model))


@dataclass
class Stats:
    period: str
    label: str
    start: datetime | None
    end: datetime
    run_id: str | None = None
    models: dict[str, str] = field(default_factory=dict)
    ads_found: int = 0
    ads_new: int = 0
    duplicates: int = 0
    filtered: int = 0
    evaluated: int = 0
    approved: int = 0
    maybe: int = 0
    skipped: int = 0
    average_score: float | None = None
    folders_by_channel: dict[str, int] = field(default_factory=dict)
    emails_sent: int = 0
    emails_held: dict[str, int] = field(default_factory=dict)
    send_unconfirmed: int = 0
    forms_filled: int = 0
    form_outcomes: dict[str, int] = field(default_factory=dict)
    submitted: int = 0
    review_queue: int = 0
    needs_review: int = 0
    sources: dict[str, SourceStats] = field(default_factory=dict)
    alert_emails_read: int = 0
    unrecognised_senders: int = 0
    adzuna: dict[str, tuple[int, int]] = field(default_factory=dict)  # label -> (used, cap)
    arbeitnow: dict[str, tuple[int, int]] = field(default_factory=dict)
    llm_calls: int = 0
    tokens: int = 0
    cost: CostSummary | None = None
    per_model: list[ModelUsage] = field(default_factory=list)
    task_usage: dict[str, dict] = field(default_factory=dict)  # task -> calls, input, cached, output, cost
    status_counts: dict[str, int] = field(default_factory=dict)
    top_missing: list[tuple[str, int]] = field(default_factory=list)
    top_bridgeable: list[tuple[str, int]] = field(default_factory=list)
    last_run_problems: list[tuple[str, str]] = field(default_factory=list)  # (kind, detail)
    employer: EmployerStats | None = None
    has_runs: bool = False

    @property
    def folders_saved(self) -> int:
        return sum(self.folders_by_channel.values())

    @property
    def approval_share(self) -> float | None:
        return self.approved / self.evaluated if self.evaluated else None

    @property
    def cost_per_application(self) -> float | None:
        if self.cost is None or self.cost.total is None or not self.folders_saved:
            return None
        return self.cost.total / self.folders_saved


def period_window(conn: sqlite3.Connection, period: str, now: datetime | None = None
                  ) -> tuple[datetime | None, datetime, str | None]:
    """``(start, end, run_id)`` for a period; ``start`` is None for all time."""
    now = now or datetime.now(UTC)
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; valid periods: {', '.join(PERIODS)}")
    if period == "run":
        row = conn.execute("SELECT id, started_at, finished_at FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
        if row is None:
            return now, now, None
        end = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else now
        return datetime.fromisoformat(row["started_at"]), end, row["id"]
    if period == "today":
        local = now.astimezone(BERLIN).replace(hour=0, minute=0, second=0, microsecond=0)
        return local.astimezone(UTC), now, None
    if period == "all":
        return None, now, None
    return now - timedelta(days=7 if period == "7d" else 30), now, None


def _between(column: str, start: datetime | None, end: datetime) -> tuple[str, tuple[str, ...]]:
    if start is None:
        return f"{column} < ?", (now_iso(end + timedelta(seconds=1)),)
    return f"{column} >= ? AND {column} < ?", (now_iso(start), now_iso(end + timedelta(seconds=1)))


def _events(conn: sqlite3.Connection, kinds: tuple[str, ...], start, end, run_id) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(kinds))
    if run_id is not None:
        return conn.execute(f"SELECT * FROM run_events WHERE run_id = ? AND kind IN ({marks}) ORDER BY id",
                            (run_id, *kinds)).fetchall()
    where, args = _between("at", start, end)
    return conn.execute(f"SELECT * FROM run_events WHERE {where} AND kind IN ({marks}) ORDER BY id",
                        (*args, *kinds)).fetchall()


def _data(row: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(row["data"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def held_reason(detail: str | None) -> str:
    text = (detail or "").lower()
    return next((label for needle, label in HELD_REASONS if needle in text), "other")


def compute_stats(conn: sqlite3.Connection, config: Config, prices: PriceTable, period: str = "today", *,
                  now: datetime | None = None, models: dict[str, str] | None = None) -> Stats:
    from autoapp.tracking.commands import _usages

    now = now or datetime.now(UTC)
    start, end, run_id = period_window(conn, period, now)
    st = Stats(period=period, label=PERIOD_LABELS[period], start=start, end=end, run_id=run_id,
               models=models or {})
    st.has_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] > 0
    if period == "run" and run_id is None:
        return _current(conn, st, config)

    def count(sql: str, column: str, extra: str = "", args: tuple = ()) -> int:
        where, wargs = _between(column, start, end)
        return int(conn.execute(f"{sql} WHERE {where}{extra}", (*wargs, *args)).fetchone()[0])

    # Ads: prefer the counts the search reported; older history falls back to the tables.
    source_events = _events(conn, ("source_done",), start, end, run_id)
    st.ads_new = count("SELECT COUNT(*) FROM jobs", "discovered_at")
    if source_events:
        for row in source_events:
            d = _data(row)
            name = str(d.get("source") or (row["detail"] or "").split(":", 1)[0] or "unknown")
            src = st.sources.setdefault(name, SourceStats())
            src.found += int(d.get("found", 0))
            src.new += int(d.get("new", 0))
            for key in ("pages", "requests", "invalid", "foreign", "uncertain"):
                if key in d:
                    src.details[key] = src.details.get(key, 0) + int(d[key])
            st.ads_found += int(d.get("found", 0))
            st.duplicates += int(d.get("duplicates", 0))
            st.unrecognised_senders += int(d.get("unrecognised", 0))
    else:
        where, wargs = _between("s.seen_at", start, end)
        for r in conn.execute(f"SELECT s.source, COUNT(*) AS n FROM job_sources s WHERE {where} GROUP BY s.source",
                              wargs):
            st.sources[r["source"]] = SourceStats(found=r["n"])
            st.ads_found += r["n"]
        where, wargs = _between("j.discovered_at", start, end)
        for r in conn.execute(
            f"SELECT s.source, COUNT(DISTINCT j.id) AS n FROM jobs j JOIN job_sources s ON s.job_id = j.id "
            f"WHERE {where} AND s.id = (SELECT MIN(id) FROM job_sources WHERE job_id = j.id) GROUP BY s.source", wargs):
            st.sources.setdefault(r["source"], SourceStats()).new = r["n"]
        st.duplicates = max(0, st.ads_found - st.ads_new)
    st.filtered = count("SELECT COUNT(*) FROM status_events", "at", " AND to_status = ?", (Status.FILTERED.value,))

    # Evaluations
    where, wargs = _between("created_at", start, end)
    evals = conn.execute(f"SELECT fit_score, decision, payload FROM evaluations WHERE {where}", wargs).fetchall()
    st.evaluated = len(evals)
    decisions = Counter(e["decision"] for e in evals)
    st.approved, st.maybe, st.skipped = decisions["apply"], decisions["maybe"], decisions["skip"]
    scores = [e["fit_score"] for e in evals if e["fit_score"] is not None]
    st.average_score = sum(scores) / len(scores) if scores else None
    missing: Counter[str] = Counter()
    bridgeable: Counter[str] = Counter()
    for e in evals:
        try:
            payload = json.loads(e["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        result = payload.get("result", payload) if isinstance(payload, dict) else {}
        for req in result.get("requirements") or []:
            text = " ".join(str(req.get("text", "")).split()).strip()
            if not text:
                continue
            if req.get("status") == "missing":
                missing[text.lower()] += 1
            elif req.get("status") == "bridgeable":
                bridgeable[text.lower()] += 1
    st.top_missing = missing.most_common(8)
    st.top_bridgeable = bridgeable.most_common(8)

    # Applications
    where, wargs = _between("prepared_at", start, end)
    for r in conn.execute(f"SELECT COALESCE(channel, 'unknown') AS c, COUNT(*) AS n FROM applications "
                          f"WHERE {where} GROUP BY c", wargs):
        st.folders_by_channel[r["c"]] = r["n"]
    st.emails_sent = count("SELECT COUNT(*) FROM send_attempts", "updated_at", " AND state = 'sent'")
    for row in _events(conn, ("email_held",), start, end, run_id):
        reason = held_reason(row["detail"])
        st.emails_held[reason] = st.emails_held.get(reason, 0) + 1
    st.forms_filled = len(_events(conn, ("form_filled",), start, end, run_id))
    latest = {}
    for row in _events(conn, ("form_resolved",), start, end, run_id):
        latest[row["job_id"]] = _data(row).get("form_status")
    st.form_outcomes = dict(Counter(status for status in latest.values() if status))
    st.submitted = count("SELECT COUNT(*) FROM status_events", "at", " AND to_status = ?", (Status.SUBMITTED.value,))
    st.alert_emails_read = count("SELECT COUNT(*) FROM processed_emails", "processed_at")

    # LLM usage
    if run_id is not None:
        usage_rows = conn.execute("SELECT * FROM llm_usage WHERE run_id = ?", (run_id,)).fetchall()
    else:
        where, wargs = _between("created_at", start, end)
        usage_rows = conn.execute(f"SELECT * FROM llm_usage WHERE {where}", wargs).fetchall()
    usages = _usages(usage_rows)
    st.llm_calls = len(usages)
    st.tokens = sum(u.input_tokens + u.output_tokens for u in usages)  # input already includes the cached part
    st.cost = prices.summarize(usages)
    st.per_model = model_usage(usages, prices)
    for u in usages:
        row = st.task_usage.setdefault(u.task, {"calls": 0, "input": 0, "cached": 0, "output": 0, "cost": 0.0})
        row["calls"] += 1
        row["input"] += u.input_tokens
        row["cached"] += u.cached_input_tokens
        row["output"] += u.output_tokens
        cost = prices.cost_of(u)
        row["cost"] = None if cost is None or row["cost"] is None else row["cost"] + cost

    st.employer = _employer_stats(conn, config, start, end, run_id, now)

    if run_id is None:
        last = conn.execute("SELECT id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
        problems_run = last["id"] if last else None
    else:
        problems_run = run_id
    if problems_run:
        st.last_run_problems = [
            (r["kind"], r["detail"] or "")
            for r in conn.execute("SELECT kind, detail FROM run_events WHERE run_id = ? AND kind IN ('warning', 'error') "
                                  "ORDER BY id DESC LIMIT 10", (problems_run,))
        ]
    return _current(conn, st, config, now)


def _employer_stats(conn: sqlite3.Connection, config: Config, start: datetime | None, end: datetime,
                    run_id: str | None, now: datetime) -> EmployerStats | None:
    cfg = config.sources.employer_careers
    saved = conn.execute("SELECT status, COUNT(*) AS n FROM employers GROUP BY status").fetchall()
    if not cfg.enabled and not saved:
        return None
    es = EmployerStats()
    for row in saved:
        if row["status"] == "verified":
            es.verified_sites = row["n"]
        elif row["status"] in ("ambiguous", "unresolved"):
            es.unverified += row["n"]
    where, wargs = _between("started_at", start, end)
    for row in conn.execute(f"SELECT status, COUNT(*) AS n FROM employer_scans WHERE {where} GROUP BY status", wargs):
        es.scans[row["status"]] = row["n"]
    for row in _events(conn, ("employer_matched",), start, end, run_id):
        es.alerts_matched += 1
        es.alerts_recovered += 1 if _data(row).get("recovered") else 0
    es.alerts_unresolved = len(_events(conn, ("employer_unresolved",), start, end, run_id))
    es.jobs_added = len(_events(conn, ("employer_job_added",), start, end, run_id))
    es.deferred = sum(int(_data(r).get("jobs", 1)) for r in _events(conn, ("employer_deferred",), start, end, run_id))
    if run_id is not None:
        usage = [_data(r) for r in _events(conn, ("employer_search_usage",), start, end, run_id)]
        es.searches = sum(int(d.get("requests", 0)) for d in usage)
        es.estimated_usd = sum(float(d.get("estimated_usd", 0.0)) for d in usage)
    else:
        sql, args = "SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = 'parallel-search'", []
        if start is not None:
            sql += " AND day >= ?"
            args.append(start.astimezone(UTC).date().isoformat())
        sql += " AND day <= ?"
        args.append(end.astimezone(UTC).date().isoformat())
        es.searches = int(conn.execute(sql, args).fetchone()[0])
        es.estimated_usd = es.searches * cfg.search_cost_per_1000_usd / 1000
    day = now.date().isoformat()

    def used(like: str) -> int:
        return int(conn.execute("SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = 'parallel-search' "
                                "AND day LIKE ?", (like,)).fetchone()[0])

    es.search_limits = {"today": (used(day), cfg.max_search_requests_per_day),
                        "this month": (used(day[:7] + "-%"), cfg.max_search_requests_per_month)}
    return es


def _current(conn: sqlite3.Connection, st: Stats, config: Config, now: datetime | None = None) -> Stats:
    """Numbers that describe the present, whatever the period."""
    now = now or datetime.now(UTC)
    st.status_counts = {r["status"]: r["n"] for r in
                        conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY n DESC")}
    st.review_queue = st.status_counts.get(Status.REVIEW.value, 0)
    closed = ("skipped", "filtered", "sent", "submitted", "interview", "offer", "rejected", "withdrawn", "expired")
    marks = ",".join("?" * len(closed))
    st.needs_review = int(conn.execute(f"SELECT COUNT(*) FROM jobs WHERE needs_review = 1 AND status NOT IN ({marks})",
                                       closed).fetchone()[0])
    st.send_unconfirmed = st.status_counts.get(Status.SEND_UNCONFIRMED.value, 0)
    cfg = config.sources.adzuna
    day = now.date().isoformat()

    def used(like: str) -> int:
        return int(conn.execute("SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = 'adzuna' AND day LIKE ?",
                                (like,)).fetchone()[0])

    st.adzuna = {"today": (used(day), cfg.max_requests_per_day), "this month": (used(day[:7] + "-%"),
                                                                                 cfg.max_requests_per_month)}
    cfg_ar = config.sources.arbeitnow
    ar_day = int(conn.execute(
        "SELECT COALESCE(SUM(requests),0) FROM api_usage WHERE source='arbeitnow' AND day=?", (day,),
    ).fetchone()[0])
    ar_month = int(conn.execute(
        "SELECT COALESCE(SUM(requests),0) FROM api_usage WHERE source='arbeitnow' AND day LIKE ?",
        (day[:7] + "-%",),
    ).fetchone()[0])
    if cfg_ar.enabled or ar_month:
        st.arbeitnow = {"today": (ar_day, cfg_ar.max_requests_per_day),
                        "this month": (ar_month, cfg_ar.max_requests_per_month)}
    return st
