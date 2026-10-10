"""A fresh test run: the pipeline runs in a separate workspace with an empty job history.

Every job found is new there, so it is evaluated, prepared and test-sent again, while the real
workspace (jobs, statuses, application folders, ``applications.csv``) is never touched. The test
workspace lives in ``workspace/test-runs/<time>/`` so its folders and letters can be looked at.

Copied in: the configuration, the profile, the writing instructions, the documents, the fetched
OpenRouter prices, the saved employer sites (so no paid lookup is repeated) and the API request
counts (so provider limits still hold). Secrets are not copied: the real ``.env`` is loaded for the
test workspace too (``Workspace.load_env``), also when its folder is opened later with ``-w``. Merged back afterwards: the API requests and the AI usage of the test run, because
both were really spent.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from autoapp.config.workspace import Workspace
from autoapp.db.schema import open_db

TEST_RUNS_DIR = "test-runs"
EMPLOYER_TABLES = ("employers", "employer_aliases", "employer_sites", "employer_scans", "employer_listings")


@dataclass
class Sandbox:
    ws: Workspace
    real: Workspace
    started_at: str
    api_before: dict[tuple[str, str], int] = field(default_factory=dict)


def _copy_rows(source: sqlite3.Connection, target: sqlite3.Connection, table: str) -> None:
    cursor = source.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed table names
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    if rows:
        marks = ", ".join("?" for _ in columns)
        target.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})",  # noqa: S608
                           [tuple(r) for r in rows])


def create(real: Workspace, now: datetime | None = None) -> Sandbox:
    """Make a new test workspace next to the real one and seed it (see the module docstring)."""
    now = now or datetime.now(UTC)
    base = real.root / TEST_RUNS_DIR / now.astimezone().strftime("%Y%m%d-%H%M%S")
    root, n = base, 1
    while root.exists():
        n += 1
        root = base.with_name(f"{base.name}-{n}")
    ws = Workspace(root)
    ws.ensure_layout()
    for path in (real.config_path, real.profile_path, real.writing_instructions_path, real.price_cache_path):
        if path.is_file():
            shutil.copy2(path, root / path.name)
    if real.documents_dir.is_dir():
        shutil.rmtree(ws.documents_dir)
        shutil.copytree(real.documents_dir, ws.documents_dir)
    sandbox = Sandbox(ws, real, now.isoformat())
    target = open_db(ws.db_path)
    try:
        if real.db_path.exists():
            source = open_db(real.db_path)
            try:
                with target:
                    for table in (*EMPLOYER_TABLES, "api_usage"):
                        _copy_rows(source, target, table)
            finally:
                source.close()
        sandbox.api_before = {(r["source"], r["day"]): r["requests"]
                              for r in target.execute("SELECT * FROM api_usage")}
    finally:
        target.close()
    return sandbox


def merge_back(sandbox: Sandbox) -> None:
    """Add the test run's API requests and AI usage to the real workspace's counts."""
    test = open_db(sandbox.ws.db_path)
    real = open_db(sandbox.real.db_path)
    try:
        with real:
            for r in test.execute("SELECT * FROM api_usage"):
                added = r["requests"] - sandbox.api_before.get((r["source"], r["day"]), 0)
                if added > 0:
                    real.execute("INSERT INTO api_usage (source, day, requests) VALUES (?, ?, ?) "
                                 "ON CONFLICT(source, day) DO UPDATE SET requests = requests + excluded.requests",
                                 (r["source"], r["day"], added))
            cursor = test.execute("SELECT * FROM llm_usage")  # the test workspace starts without any
            columns = [d[0] for d in cursor.description if d[0] != "id"]
            for r in cursor.fetchall():
                real.execute(f"INSERT INTO llm_usage ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",  # noqa: S608
                             tuple(r[c] for c in columns))
    finally:
        test.close()
        real.close()


def describe(sandbox: Sandbox) -> str:
    return (f"Fresh test run: everything was done in {sandbox.ws.root} (your job history, folders and CSV were not "
            "touched). Delete that folder when you no longer need it.")
