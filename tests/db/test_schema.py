from __future__ import annotations

import sqlite3

import pytest

from autoapp.db import schema
from autoapp.db.schema import current_version, open_db

TABLES = {
    "jobs", "job_sources", "job_locations", "evaluations", "applications",
    "send_attempts", "status_events", "processed_emails", "llm_usage", "runs", "judge_reviews",
}  # fmt: skip


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def test_fresh_database_has_all_tables(tmp_path):
    conn = open_db(tmp_path / "sub" / "autoapp.db")
    assert TABLES <= _tables(conn)
    assert current_version(conn) == len(schema.MIGRATIONS)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.row_factory is sqlite3.Row


def test_reopen_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "a.db"
    conn = open_db(path)
    conn.execute(
        "INSERT INTO jobs (id, title, company, discovered_at, updated_at) VALUES ('abc123', 't', 'c', 'x', 'x')"
    )
    conn.commit()
    conn.close()
    conn = open_db(path)
    assert current_version(conn) == len(schema.MIGRATIONS)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_migrates_from_version_zero(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()
    conn = open_db(path)
    assert current_version(conn) == len(schema.MIGRATIONS) >= 1
    assert "jobs" in _tables(conn)


def test_migrates_version_eight_by_adding_judge_reviews_without_losing_evaluations(tmp_path):
    path = tmp_path / "v8.db"
    raw = sqlite3.connect(path)
    for n, step in enumerate(schema.MIGRATIONS[:8]):
        raw.executescript(f"BEGIN;\n{step}\nPRAGMA user_version = {n + 1};\nCOMMIT;")
    raw.execute("INSERT INTO jobs (id, title, company, discovered_at, updated_at) VALUES ('j1', 'Backend', 'Acme', 'x', 'x')")
    raw.execute("INSERT INTO evaluations (job_id, created_at, fit_score, decision, payload) "
                "VALUES ('j1', 'x', 80, 'apply', '{}')")
    raw.commit()
    raw.close()

    conn = open_db(path)
    evaluation_id = conn.execute("SELECT id FROM evaluations WHERE job_id = 'j1'").fetchone()[0]
    conn.execute("INSERT INTO judge_reviews (evaluation_id, run_id, created_at, provider, model, score, agrees, payload) "
                 "VALUES (?, 'r1', 'x', 'fake', 'fake-model', 90, 1, '{}')", (evaluation_id,))
    assert current_version(conn) == 9
    assert conn.execute("SELECT score FROM judge_reviews WHERE evaluation_id = ?", (evaluation_id,)).fetchone()[0] == 90
    assert conn.execute("SELECT COUNT(*) FROM evaluations WHERE job_id = 'j1'").fetchone()[0] == 1


def test_migrates_stepwise(tmp_path, monkeypatch):
    path = tmp_path / "step.db"
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])
    open_db(path).close()
    monkeypatch.setattr(schema, "MIGRATIONS", [*schema.MIGRATIONS, "CREATE TABLE extra (x INTEGER);"])
    conn = open_db(path)
    assert current_version(conn) == 2
    assert "extra" in _tables(conn)


def test_failed_step_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "bad.db"
    released = len(schema.MIGRATIONS)
    monkeypatch.setattr(schema, "MIGRATIONS", [*schema.MIGRATIONS, "CREATE TABLE t (x); BOGUS SQL;"])
    with pytest.raises(sqlite3.Error):
        open_db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == released
    assert raw.execute("SELECT name FROM sqlite_master WHERE name = 't'").fetchone() is None


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "new.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(RuntimeError, match="newer"):
        open_db(path)


def test_source_uniqueness(tmp_path):
    conn = open_db(tmp_path / "u.db")
    conn.execute("INSERT INTO jobs (id, title, company, discovered_at, updated_at) VALUES ('a', 't', 'c', 'x', 'x')")
    ins = "INSERT INTO job_sources (job_id, source, source_job_id, seen_at) VALUES ('a', 'adzuna', '1', 'x')"
    conn.execute(ins)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins)
