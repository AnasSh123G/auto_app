"""Delete everything the app has collected, and keep the setup (``autoapp reset-data``, Settings menu).

Deleted: the job history (``autoapp.db``: jobs, evaluations, sent mails, processed alert emails, employer lookups,
statistics), the application folders, ``applications.csv``, the logs and the fresh test runs.
Kept: ``config.yaml``, ``profile.yaml``, ``.env``, ``documents/``, ``writing_instructions.md`` and ``backups/``, the saved model prices;
also your saved form answers (``form_answers.md``) and the browser logins (``browser-profile/``) unless you ask to
delete them too. Nothing is deleted without a confirmation.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autoapp.config.workspace import Workspace
from autoapp.steps.sandbox import TEST_RUNS_DIR


@dataclass
class Plan:
    delete: list[tuple[Path, str]] = field(default_factory=list)  # (path, what it is)
    keep: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.delete


def _jobs_in(db: Path) -> str:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            conn.close()
        return f"{n} jobs"
    except sqlite3.Error:
        return "unreadable"


def _count(path: Path) -> int:
    return sum(1 for _ in path.iterdir()) if path.is_dir() else 0


def plan(ws: Workspace, *, answers: bool = False, logins: bool = False) -> Plan:
    """What would be deleted and what is kept. ``answers``/``logins``: also delete those."""
    p = Plan()
    db = ws.db_path
    if db.exists():
        p.delete.append((db, f"the job history ({_jobs_in(db)}, evaluations, sent mails, processed alert emails, "
                             "employer lookups, statistics)"))
    for suffix in ("-wal", "-shm", "-journal"):
        extra = db.with_name(db.name + suffix)
        if extra.exists():
            p.delete.append((extra, "part of the job history"))
    runs = ws.root / TEST_RUNS_DIR
    for path, what in ((ws.applications_dir, f"the application folders ({_count(ws.applications_dir)})"),
                       (ws.csv_path, "the applications list"),
                       (ws.logs_dir, f"the logs ({_count(ws.logs_dir)} files)"),
                       (runs, f"the fresh test runs ({_count(runs)})")):
        if path.exists() and (path.is_file() or _count(path)):
            p.delete.append((path, what))
    for path, what, wanted in ((ws.form_answers_path, "your saved form answers", answers),
                               (ws.browser_profile_dir, "the browser logins (LinkedIn, Workwise, ...)", logins)):
        if path.exists():
            (p.delete if wanted else p.keep).append((path, what))
    for path, what in ((ws.config_path, "settings"), (ws.profile_path, "your profile"), (ws.env_path, "passwords and keys"),
                       (ws.price_cache_path, "the saved model prices"), (ws.documents_dir, "your documents"), (ws.writing_instructions_path, "writing instructions"),
                       (ws.backups_dir, "setup backups")):
        if path.exists():
            p.keep.append((path, what))
    return p


def describe(ws: Workspace, p: Plan) -> list[str]:
    def rel(path: Path) -> str:
        return str(path.relative_to(ws.root)) + ("/" if path.is_dir() else "")

    lines = ["This deletes:"] + [f"  - {rel(path)}: {what}" for path, what in p.delete]
    lines += ["It keeps:"] + [f"  - {rel(path)}: {what}" for path, what in p.keep]
    return lines


def execute(ws: Workspace, p: Plan) -> list[str]:
    """Delete what the plan lists; return the problems (empty when all went)."""
    problems = []
    for path, _ in p.delete:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as err:
            problems.append(f"{path}: {err}")
    ws.ensure_layout()
    return problems


def run(ws: Workspace, *, say: Callable[[str], None], confirm: Callable[[str], bool], answers: bool = False,
        logins: bool = False, assume_yes: bool = False) -> bool:
    """Show what goes, ask once (unless ``assume_yes``), then delete under the workspace lock. True when deleted."""
    from autoapp.lock import workspace_lock

    p = plan(ws, answers=answers, logins=logins)
    if p.empty:
        say("There is no job data to delete. Your setup is unchanged.")
        return False
    for line in describe(ws, p):
        say(line)
    if not assume_yes and not confirm("Delete this for good? It cannot be undone."):
        say("Nothing was deleted.")
        return False
    with workspace_lock(ws):
        problems = execute(ws, p)
    for problem in problems:
        say(f"could not delete {problem}")
    say("Deleted. Your setup is unchanged; the next run starts from an empty job history." if not problems
        else "Partly deleted (see above).")
    return not problems
