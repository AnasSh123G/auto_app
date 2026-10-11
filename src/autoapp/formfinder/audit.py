"""Read-only audit of the prepared form applications: how many links were found, and where the others got stuck.

``audit(conn, restricted_domains)`` only reads. ``open_readonly`` opens the workspace database with SQLite's
``mode=ro`` so that nothing here can write, whatever the caller does.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path

from autoapp.assist.filler import is_restricted
from autoapp.employers.scope import bare_host, is_directory

UNCHECKED = "unchecked"
_ORDER = ("found", "partial", "none", "needs_browser", "login_required", "captcha", "restricted", "no_employer_link",
          "unreachable", UNCHECKED)


def open_readonly(db_path: Path) -> sqlite3.Connection | None:
    """The database opened read-only, or None when the workspace has none yet."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed names


def _cause(status: str, note: str | None) -> str:
    """The note without the parts that differ per job (hosts, numbers), so equal causes group together."""
    text = re.split(r"[;:(]", note or "", maxsplit=1)[0].strip().lower()
    return re.sub(r"\d+", "N", text)[:60] or "no note"


def empty() -> dict:
    return {"prepared": 0, "by_status": {}, "on_job_board": {"total": 0, "by_host": {}}, "by_source": {},
            "failures": []}


def audit(conn: sqlite3.Connection, restricted_domains: list[str] | tuple[str, ...]) -> dict:
    """The audit as plain data (also the ``--json`` output); see docs/assisted-applying.md for the shape."""
    result = empty()
    app_columns = _columns(conn, "applications")
    status_sql = "a.form_status" if "form_status" in app_columns else "NULL"
    note_sql = "a.form_note" if "form_note" in app_columns else "NULL"
    rows = conn.execute(
        f"SELECT j.id, j.application_url, {status_sql} AS form_status, {note_sql} AS form_note "  # noqa: S608
        "FROM jobs j JOIN applications a ON a.job_id = j.id "
        "WHERE j.status = 'prepared' AND a.channel != 'email' AND a.folder IS NOT NULL ORDER BY j.id").fetchall()
    sources: dict[str, list[str]] = {}
    for r in conn.execute("SELECT job_id, source FROM job_sources ORDER BY id"):
        sources.setdefault(r["job_id"], []).append(r["source"])
    traces: dict[str, sqlite3.Row] = {}
    if "form_attempts" in {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}:
        for r in conn.execute("SELECT job_id, status, note FROM form_attempts ORDER BY id"):
            traces[r["job_id"]] = r  # the newest attempt wins
    by_status: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    by_source: dict[str, dict[str, int]] = {}
    causes: Counter[tuple[str, str]] = Counter()
    for r in rows:
        status = r["form_status"] or UNCHECKED
        by_status[status] += 1
        url = r["application_url"] or ""
        if url and (is_directory(url) or is_restricted(url, restricted_domains)):
            hosts[bare_host(url)] += 1
        for source in dict.fromkeys(sources.get(r["id"], ["unknown"])):
            entry = by_source.setdefault(source, {"total": 0, "found": 0})
            entry["total"] += 1
            entry["found"] += status == "found"
        if status not in ("found", UNCHECKED):
            trace = traces.get(r["id"])
            causes[(status, _cause(status, trace["note"] if trace else r["form_note"]))] += 1
    result["prepared"] = len(rows)
    result["by_status"] = {s: by_status[s] for s in sorted(by_status, key=lambda s: (_rank(s), s))}
    result["on_job_board"] = {"total": sum(hosts.values()),
                              "by_host": dict(sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0])))}
    result["by_source"] = dict(sorted(by_source.items(), key=lambda kv: (-kv[1]["total"], kv[0])))
    result["failures"] = [{"status": s, "cause": c, "count": n}
                          for (s, c), n in sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))]
    return result


def _rank(status: str) -> int:
    return _ORDER.index(status) if status in _ORDER else len(_ORDER)


def lines(data: dict, where: str = "") -> list[str]:
    """The audit as the text printed by ``autoapp forms --audit`` and the quick test."""
    total = data["prepared"]
    out = [f"existing form links ({where + ', ' if where else ''}{total} prepared):"]
    if not total:
        return out
    status = data["by_status"]
    found = status.get("found", 0)
    parts = [f"found {found} ({round(100 * found / total)}%)"]
    parts += [f"{name.replace('_', ' ')} {n}" for name, n in status.items() if name != "found"]
    board = data["on_job_board"]
    detail = ", ".join(f"{h} {n}" for h, n in board["by_host"].items())
    parts.append(f"on job board: {board['total']}" + (f" ({detail})" if detail else ""))
    out.append("  " + " · ".join(parts))
    out.append("  by source: " + " · ".join(f"{s} {v['total']} (found {v['found']})"
                                            for s, v in data["by_source"].items()))
    if data["failures"]:
        out.append("  causes: " + " · ".join(f"{f['status']}: {f['cause']} {f['count']}" for f in data["failures"]))
    return out
