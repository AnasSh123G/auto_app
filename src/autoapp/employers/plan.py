"""Read-only views of employer discovery: what is saved, and what a discovery run would do.

Nothing here migrates, creates or writes a workspace: the database is opened read-only (and only if it
exists), the configuration and ``.env`` are only read, and no search, page or model request is made.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import dotenv_values

from autoapp.config.models import ConfigError, load_config
from autoapp.config.workspace import Workspace
from autoapp.employers.repo import EmployerRepo
from autoapp.employers.resolver import find_saved, group_seeds
from autoapp.employers.service import eligible_seed_rows, seed_of
from autoapp.employers.verify import is_unnamed_client


def open_readonly(ws: Workspace) -> sqlite3.Connection | None:
    """The workspace database opened read-only, or ``None`` when there is none."""
    if not ws.db_path.exists():
        return None
    # With no write-ahead log there is nothing to replay, and "immutable" keeps SQLite from creating the
    # -wal/-shm helper files; with one, a plain read-only open reads it without writing.
    wal = Path(str(ws.db_path.resolve()) + "-wal")
    flags = "?mode=ro" if wal.exists() else "?mode=ro&immutable=1"
    conn = sqlite3.connect(ws.db_path.resolve().as_uri() + flags, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_employer_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'employers'").fetchone() is not None


def _short(ts: str | None) -> str:
    return (ts or "")[:16].replace("T", " ")


def describe_saved(ws: Workspace) -> list[str]:
    """Saved employers, their sites, last scans and alert-job outcomes (read-only)."""
    conn = open_readonly(ws)
    if conn is None or not _has_employer_tables(conn):
        if conn is not None:
            conn.close()
        return ["No employer sites saved yet. Enable `sources.employer_careers` and run `autoapp employers --discover`."]
    try:
        repo = EmployerRepo(conn)
        rows = repo.all()
        if not rows:
            return ["No employer sites saved yet."]
        out = [f"Employer sites ({len(rows)} saved)"]
        for e in rows:
            tag = e["status"] + (f", {e['provenance']}" if e["provenance"] else "") + (", confirmed by you" if e["pinned"] else "")
            out.append(f"{e['display_name']}  [{e['id']}]  {tag}")
            if e["website"]:
                out.append(f"    website: {e['website']}")
            for site in repo.sites(e["id"]):
                if site["kind"] == "website":
                    continue
                extra = f", {site['status']}" + (f" ({site['failure_reason']})" if site["failure_reason"] else "")
                via = f", linked from {site['referrer_url']}" if site["referrer_url"] else ""
                out.append(f"    {site['kind']}: {site['url']}{extra}{via}")
            if e["failure_reason"]:
                out.append(f"    last problem ({_short(e['failure_at'])}): {e['failure_reason']}")
            scan = repo.latest_scan(e["id"])
            if scan is not None:
                why = f" ({scan['reason']})" if scan["reason"] else ""
                out.append(f"    last scan: {scan['status']}, {_short(scan['finished_at'] or scan['started_at'])}, "
                           f"{scan['pages']} pages, {scan['openings']} openings{why}")
            seeds = repo.seeds_for(e["id"])
            if seeds:
                counts: dict[str, int] = {}
                for seed in seeds:
                    counts[seed["outcome"]] = counts.get(seed["outcome"], 0) + 1
                out.append("    alert jobs: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
                for seed in seeds:
                    if seed["outcome"] in ("unresolved", "conflict") and seed["reason"]:
                        cands = ", ".join(json.loads(seed["candidates"] or "[]")[:3])
                        out.append(f"      {seed['job_id']}: {seed['reason']}" + (f" (candidates: {cands})" if cands else ""))
        used = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = 'parallel-search'").fetchone()[0]
        out.append(f"Searches made so far: {used} (estimate at the configured rate, gross: see `autoapp stats`).")
        return out
    finally:
        conn.close()


def plan_discovery(ws: Workspace, *, job_ids: list[str] | None = None, profile: str | None = None, again: bool = False,
                   refresh_site: bool = False, website: str | None = None, today: datetime | None = None) -> list[str]:
    """What ``employers --discover`` would do, without any request, write, migration or setup."""
    if not ws.config_path.exists():
        return ["No workspace configuration found; there is nothing to discover. Nothing was changed."]
    try:
        config = load_config(ws)
    except ConfigError as err:
        return [f"error: {line}" for line in err.errors]
    cfg = config.sources.employer_careers
    conn = open_readonly(ws)
    if conn is None:
        return ["No jobs saved yet; nothing to discover. Nothing was changed."]
    now = today or datetime.now(UTC)
    try:
        profiles = [p for p in config.search_profiles if profile in (None, p.name)]
        rows = eligible_seed_rows(conn, job_ids=job_ids, profiles=profiles, again=again)
        if not rows:
            return ["No eligible alert jobs (unfiltered, not sent or closed) for this selection. Nothing was changed."]
        groups = group_seeds([seed_of(r) for r in rows])
        has_tables = _has_employer_tables(conn)
        repo = EmployerRepo(conn)
        env = dotenv_values(ws.env_path) if Path(ws.env_path).exists() else {}
        import os

        key = bool((os.environ.get("PARALLEL_API_KEY") or env.get("PARALLEL_API_KEY") or "").strip())
        day = now.date().isoformat()
        used_day = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = 'parallel-search' AND day = ?",
                                (day,)).fetchone()[0]
        used_month = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = 'parallel-search' "
                                  "AND day LIKE ?", (day[:7] + "-%",)).fetchone()[0]
        out = [f"Dry run: {len(rows)} alert jobs from {len(groups)} employers selected. "
               "No search, page or model request is made and nothing is written."]
        for index, group in enumerate(groups):
            jobs = ", ".join(s.job_id for s in group.seeds)
            head = f"{group.name} ({len(group.seeds)} alert job{'s' if len(group.seeds) != 1 else ''}: {jobs})"
            if is_unnamed_client(group.name):
                out.append(f"{head}: skipped, the alert does not name the employer")
                continue
            if index >= cfg.max_employers_per_run:
                out.append(f"{head}: deferred, the limit of {cfg.max_employers_per_run} employers per run is reached")
                continue
            saved = find_saved(repo, group) if has_tables else None
            if website:
                out.append(f"{head}: would confirm {website} as the employer's website, then scan it")
            elif saved == "ambiguous":
                out.append(f"{head}: several saved employers share this name; confirm one with --website")
            elif saved is not None and saved["status"] == "verified" and not refresh_site:
                scan = repo.fresh_complete_scan(saved["id"], cfg.scan_ttl_hours, now) if not again else None
                if scan is not None:
                    out.append(f"{head}: saved site {saved['website']}; complete scan from {_short(scan['finished_at'])} is "
                               "still fresh, so no request would be made")
                else:
                    out.append(f"{head}: saved site {saved['website']}; would scan it again (up to {cfg.max_pages_per_employer} "
                               "pages, no search)")
            elif (saved is not None and saved["status"] in ("ambiguous", "unresolved") and not again and not refresh_site
                  and repo.failure_is_fresh(saved, cfg.failure_ttl_hours, now)):
                retry = datetime.fromisoformat(saved["failure_at"]) + timedelta(hours=cfg.failure_ttl_hours)
                out.append(f"{head}: skipped, previously {saved['status']} ({saved['failure_reason']}); "
                           f"retry after {_short(retry.isoformat())} or with --again")
            else:
                action = "would look up the official site (up to 2 searches in Fast mode), verify it, then scan it"
                if not key:
                    action = "would be deferred: PARALLEL_API_KEY is not set, so unknown employers are not looked up"
                elif used_day >= cfg.max_search_requests_per_day or used_month >= cfg.max_search_requests_per_month:
                    action = "would be deferred: the daily or monthly search limit is used up"
                out.append(f"{head}: {action}")
        out.append(f"Searches used: {used_day} of {cfg.max_search_requests_per_day} today, "
                   f"{used_month} of {cfg.max_search_requests_per_month} this month.")
        return out
    finally:
        conn.close()
