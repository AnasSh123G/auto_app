"""The statistics page and the run watcher (tasks 14.4, 14.7, 14.8).

Both only read the database: they never take the workspace lock, so they can run next to a
scheduled run. A run whose progress has not changed for ``STALE_AFTER`` is shown as stale.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.live import Live

from autoapp.config.models import load_config
from autoapp.config.workspace import Workspace
from autoapp.dashboard.keys import KeyReader
from autoapp.dashboard.queries import PERIODS, compute_stats
from autoapp.dashboard.view import make_console, render_run_view, render_stats_page
from autoapp.dashboard.wiring import models_in_use
from autoapp.db.schema import open_db
from autoapp.events import RunEvent, RunStats
from autoapp.llm.cost import PriceTable

STALE_AFTER = timedelta(minutes=15)
FEED_KEEP = 200


def _open(ws: Workspace) -> sqlite3.Connection:
    return open_db(ws.db_path)


def active_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The newest unfinished run that reports progress, or None."""
    return conn.execute(
        "SELECT * FROM runs WHERE finished_at IS NULL AND progress IS NOT NULL ORDER BY started_at DESC, rowid DESC LIMIT 1"
    ).fetchone()


def load_run_snapshot(conn: sqlite3.Connection, run_id: str, now: datetime | None = None
                      ) -> tuple[RunStats, list[RunEvent], bool]:
    """Counters, the newest feed events and whether the run is stale, from the database."""
    now = now or datetime.now(UTC)
    row = conn.execute("SELECT progress, finished_at FROM runs WHERE id = ?", (run_id,)).fetchone()
    progress = json.loads(row["progress"]) if row and row["progress"] else {}
    stats = RunStats.from_dict(progress.get("stats", {}))
    if row is not None and row["finished_at"]:
        stats.finished = True
        stats.step = None
    updated = progress.get("updated_at")
    stale = bool(updated) and not stats.finished and now - datetime.fromisoformat(updated) > STALE_AFTER
    rows = conn.execute("SELECT * FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                        (run_id, FEED_KEEP)).fetchall()
    events = []
    for r in reversed(rows):
        try:
            data = json.loads(r["data"] or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        events.append(RunEvent(kind=r["kind"], job_id=r["job_id"], company=r["company"], title=r["title"],
                               detail=r["detail"], data=data, at=datetime.fromisoformat(r["at"])))
    return stats, events, stale


def show_stats(ws: Workspace, period: str = "today", *, interactive: bool = True, console: Console | None = None,
               keys=None, refresh_s: float = 5.0, now: Callable[[], datetime] | None = None) -> None:
    """Show the statistics page. With an active run and a terminal, show that run live instead."""
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; valid periods: {', '.join(PERIODS)}")
    console = console or make_console()
    clock = now or (lambda: datetime.now(UTC))
    config = load_config(ws)
    from autoapp.llm.openrouter_prices import auto_refresh

    if note := auto_refresh(ws.price_cache_path, config.llm):
        console.print(note, style="yellow")
    prices = PriceTable.for_workspace(ws, config.llm.prices)
    models = models_in_use(config.llm)
    conn = _open(ws)
    try:
        if interactive and active_run(conn) is not None:
            conn.close()
            watch_run(ws, console=console, keys=keys, now=clock)
            return
        stats = compute_stats(conn, config, prices, period, now=clock(), models=models)
        if not interactive:
            console.print(render_stats_page(stats, console.width))
            return
        keys = keys or KeyReader()
        with keys, Live(console=console, auto_refresh=False, transient=False) as live:
            last = time.monotonic()
            live.update(render_stats_page(stats, console.width, interactive=True), refresh=True)
            while True:
                key = keys.read(0.5)
                if key in ("q", "Q", "\x1b", "\r", "\n"):
                    return
                if key in ("p", "P"):
                    period = PERIODS[(PERIODS.index(period) + 1) % len(PERIODS)]
                elif key is None and time.monotonic() - last < refresh_s:
                    if not getattr(keys, "enabled", True):
                        return  # no keyboard: nothing more to do
                    continue
                last = time.monotonic()
                stats = compute_stats(conn, config, prices, period, now=clock(), models=models)
                live.update(render_stats_page(stats, console.width, interactive=True), refresh=True)
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass


def watch_run(ws: Workspace, run_id: str | None = None, *, console: Console | None = None, keys=None,
              interval_s: float = 1.0, now: Callable[[], datetime] | None = None,
              max_refreshes: int | None = None) -> bool:
    """Show a run from the database, refreshing about once per second. Returns whether one was shown.

    Read-only and lock-free: closing it never affects the run.
    """
    console = console or make_console()
    clock = now or (lambda: datetime.now(UTC))
    conn = _open(ws)
    try:
        if run_id is None:
            row = active_run(conn) or conn.execute("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
            if row is None:
                console.print("No run to show yet. Start one with `autoapp run`.")
                return False
            run_id = row["id"]
        only_forms = False
        keys = keys or KeyReader()
        refreshes = 0
        with keys, Live(console=console, auto_refresh=False, transient=False) as live:
            while True:
                stats, events, stale = load_run_snapshot(conn, run_id, clock())
                live.update(render_run_view(stats, events, console.width, console.height,
                                            only_emails_and_forms=only_forms, final=stats.finished, stale=stale,
                                            now=clock()), refresh=True)
                refreshes += 1
                if max_refreshes is not None and refreshes >= max_refreshes:
                    return True
                if not getattr(keys, "enabled", True):
                    return True  # no terminal: one snapshot
                key = keys.read(interval_s)
                if stats.finished and key is not None:
                    return True
                if key in ("q", "Q", "\x1b"):
                    return True
                if key in ("f", "F"):
                    only_forms = not only_forms
    finally:
        conn.close()
