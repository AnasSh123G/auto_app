"""The read-only form-link audit (`autoapp forms --audit`) and the form_attempts traces."""

import hashlib
import json
import sqlite3

import httpx
import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.db.schema import MIGRATIONS, migrate, open_db
from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.formfinder import audit as form_audit
from autoapp.formfinder.finder import find
from autoapp.formfinder.store import resolve
from autoapp.steps.context import RunContext
from tests.helpers import make_workspace

runner = CliRunner()


def checksum(path):
    """The database file and a dump of every row. (Opening a WAL database may create empty -wal/-shm files.)"""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    return hashlib.sha256(path.read_bytes()).hexdigest(), hashlib.sha256(dump.encode()).hexdigest()


@pytest.fixture
def ctx(tmp_path):
    context = RunContext.open(make_workspace(tmp_path))
    yield context
    context.close()


def prepared(ctx, url, source, status=None, note=None, channel="form", title="Dev"):
    jid = ctx.repo.insert_job(title=title, company="Muster GmbH", application_url=url, job_url=url)
    ctx.repo.add_source(jid, source, f"s-{jid}")
    ctx.conn.execute("UPDATE jobs SET status = 'prepared' WHERE id = ?", (jid,))
    ctx.repo.upsert_application(jid, channel=channel, folder="f", form_status=status, form_note=note)
    return jid


def seed(ctx):
    prepared(ctx, "https://careers.acme.de/apply/1", "arbeitsagentur", "found")
    prepared(ctx, "https://www.arbeitsagentur.de/job/2", "arbeitsagentur", "partial", "no usable application continuation")
    prepared(ctx, "https://www.linkedin.com/jobs/3", "linkedin-alert", "restricted", "restricted target: linkedin.com")
    prepared(ctx, "https://x.example/4", "linkedin-alert", "partial", "no usable application continuation")
    prepared(ctx, "https://x.example/5", "stepstone-alert", None)
    prepared(ctx, "https://x.example/6", "stepstone-alert", "found", channel="email")  # email: not counted
    ctx.conn.commit()


def test_audit_counts_and_leaves_the_database_unchanged(ctx):
    seed(ctx)
    ctx.close()
    path = ctx.ws.db_path
    before = checksum(path)
    result = runner.invoke(app, ["-w", str(ctx.ws.root), "forms", "--audit", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["prepared"] == 5
    assert data["by_status"] == {"found": 1, "partial": 2, "restricted": 1, "unchecked": 1}
    assert data["on_job_board"] == {"total": 2, "by_host": {"arbeitsagentur.de": 1, "linkedin.com": 1}}
    assert data["by_source"]["arbeitsagentur"] == {"total": 2, "found": 1}
    assert data["by_source"]["stepstone-alert"] == {"total": 1, "found": 0}
    assert {"status": "partial", "cause": "no usable application continuation", "count": 2} in data["failures"]
    assert checksum(path) == before
    text = runner.invoke(app, ["-w", str(ctx.ws.root), "forms", "--audit"]).output
    assert "existing form links (5 prepared):" in text and "found 1 (20%)" in text and "on job board: 2" in text
    assert checksum(path) == before


def test_audit_options_and_missing_database(tmp_path):
    ws = make_workspace(tmp_path)
    assert runner.invoke(app, ["-w", str(ws.root), "forms", "--json"]).exit_code == 2
    assert runner.invoke(app, ["-w", str(ws.root), "forms", "--audit", "--again"]).exit_code == 2
    result = runner.invoke(app, ["-w", str(ws.root), "forms", "--audit", "--json"])
    assert result.exit_code == 0 and json.loads(result.output)["prepared"] == 0
    assert not ws.db_path.exists()


def test_audit_reads_a_workspace_from_before_form_status(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    for step in MIGRATIONS[:4]:
        raw.executescript(step)
    raw.execute("INSERT INTO jobs (id, title, company, discovered_at, updated_at, status) VALUES ('a','t','c','x','x','prepared')")
    raw.execute("INSERT INTO applications (job_id, channel, folder) VALUES ('a', 'form', 'f')")
    raw.commit()
    raw.close()
    conn = form_audit.open_readonly(path)
    data = form_audit.audit(conn, [])
    assert data["by_status"] == {"unchecked": 1}
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM jobs")


def test_the_quick_test_audit_is_read_only(tmp_path, capsys):
    ws = make_workspace(tmp_path)
    context = RunContext.open(ws)
    seed(context)
    context.close()
    from autoapp import commands

    before = checksum(ws.db_path)
    commands._echo_form_audit(ws)
    assert "existing form links (real workspace, 5 prepared):" in capsys.readouterr().out
    assert checksum(ws.db_path) == before


def test_migration_creates_form_attempts_and_checks_the_strategy(tmp_path):
    conn = open_db(tmp_path / "a.db")
    assert migrate(conn) == len(MIGRATIONS)
    conn.execute("INSERT INTO jobs (id, title, company, discovered_at, updated_at) VALUES ('a','t','c','x','x')")
    conn.execute("INSERT INTO form_attempts (job_id, strategy, hops, status, duration_ms, created_at) "
                 "VALUES ('a', 'ats', '[]', 'found', 1, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO form_attempts (job_id, strategy, hops, status, duration_ms, created_at) "
                     "VALUES ('a', 'magic', '[]', 'found', 1, 'x')")


def test_find_records_a_trace_with_hops_and_resolve_passes_the_job(ctx):
    pages = {"/ad": '<a href="/bewerben">Jetzt bewerben</a>',
             "/bewerben": "<form><input type='text' name='name'><input type='file' name='cv'></form>"}
    ctx._http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text=pages[r.url.path]))), 0, 0)
    jid = prepared(ctx, "https://acme.example/ad", "stepstone-alert")
    job = ctx.repo.get_job(jid)
    outcome = resolve(ctx, job, again=True)
    rows = ctx.conn.execute("SELECT * FROM form_attempts WHERE job_id = ?", (jid,)).fetchall()
    assert len(rows) == 1 and rows[0]["strategy"] == "http" and rows[0]["status"] == outcome.status
    hops = json.loads(rows[0]["hops"])
    assert hops[0] == {"url": "https://acme.example/ad", "reason": "entry"}
    assert all(set(h) == {"url", "reason"} for h in hops) and rows[0]["duration_ms"] >= 0
    assert ctx.repo.get_application(jid)["form_note"]  # the one-line summary stays
    find(ctx, "https://acme.example/ad")  # without a job nothing is recorded
    assert ctx.conn.execute("SELECT COUNT(*) FROM form_attempts").fetchone()[0] == 1


def test_a_failed_attempt_is_grouped_by_cause(ctx):
    ctx._http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<p>nothing</p>"))), 0, 0)
    jid = prepared(ctx, "https://acme.example/ad", "stepstone-alert")
    resolve(ctx, ctx.repo.get_job(jid), again=True)
    ctx.conn.commit()
    data = form_audit.audit(ctx.conn, [])
    assert data["failures"] == [{"status": "none", "cause": "no usable application continuation", "count": 1}]
