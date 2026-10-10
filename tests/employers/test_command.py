"""`autoapp employers` (tasks 5.3, 5.4)."""

from __future__ import annotations

import hashlib

import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.lock import workspace_lock
from autoapp.steps.context import RunContext
from tests.employers.identity_world import search_client
from tests.employers.test_service import CBC, EMP_CONFIG, add_alert, cbc_pages
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace

runner = CliRunner()


def invoke(ws, *args):
    return runner.invoke(app, ["-w", str(ws.root), "employers", *args])


def tree(root):
    out = {}
    for p in sorted(root.rglob("*")):
        out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
    return out


SETUP = """
setup:
  version: 1
  completed_sections: [documents, profile, salary, search, sources, llm, email, writing]
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    ws = make_workspace(tmp_path, config=EMP_CONFIG + SETUP)
    ctx = RunContext.open(ws)
    alert = add_alert(ctx)
    ctx.close()
    web = FakeWeb(cbc_pages())
    api, calls = search_client(["https://www.cbc-cologne.de/karriere"])
    monkeypatch.setattr("autoapp.employers.service.make_fetch", lambda ctx: (web, lambda: None))
    monkeypatch.setattr("autoapp.employers.service.ParallelSearch", lambda key: api)
    api.close = lambda: None
    monkeypatch.setenv("PARALLEL_API_KEY", "pk-test")
    return ws, alert, web, calls


@pytest.mark.parametrize("args", [
    ["--refresh-site"], ["--again"], ["--job", "x"], ["--profile", "backend"], ["--dry-run"],
    ["--website", "https://acme.example"], ["--website", "https://acme.example", "--job", "a", "--job", "b"],
    ["--website", "https://acme.example", "--job", "a", "--refresh-site", "--discover"],
    ["--website", "https://acme.example", "--job", "a", "--profile", "x"],
])
def test_bad_combinations_are_rejected_with_exit_2(world, args):
    ws, *_ = world
    result = invoke(ws, *args)
    assert result.exit_code == 2 and "error:" in result.output


def test_unsafe_website_is_rejected(world):
    ws, alert, *_ = world
    for url in ("https://www.linkedin.com/company/cbc", "http://127.0.0.1/", "https://user:pw@cbc.example/", "ftp://cbc.example"):
        result = invoke(ws, "--website", url, "--job", alert)
        assert result.exit_code == 2 and "cannot use" in result.output


def test_default_is_read_only_inspection(world):
    ws, alert, web, calls = world
    assert "No employer sites saved yet" in invoke(ws).output
    assert invoke(ws, "--discover").exit_code == 0
    before = tree(ws.root)
    result = invoke(ws)
    assert result.exit_code == 0 and CBC in result.output and "https://www.cbc-cologne.de/" in result.output
    assert "last scan: complete" in result.output and "1 matched" in result.output
    assert "ats: https://cbc-cologne.onapply.de/" in result.output
    assert tree(ws.root) == before


def test_discover_job_again_and_refresh_semantics(world):
    ws, alert, web, calls = world
    first = invoke(ws, "--discover", "--job", alert)
    assert first.exit_code == 0 and "1 matched" in first.output and len(calls) == 1
    again = invoke(ws, "--discover", "--job", alert, "--again")
    assert again.exit_code == 0 and len(calls) == 1  # re-scan only: the saved website is reused, no new search
    assert web.calls.count("https://www.cbc-cologne.de/karriere") >= 1
    refresh = invoke(ws, "--discover", "--job", alert, "--refresh-site")
    assert refresh.exit_code == 0 and len(calls) == 2  # an explicit request to look the website up again


def test_unknown_job_and_profile_and_nothing_to_do(world):
    ws, *_ = world
    assert invoke(ws, "--discover", "--job", "nope").exit_code == 2
    assert invoke(ws, "--discover", "--profile", "nope").exit_code == 2
    out = invoke(ws, "--discover", "--profile", "backend")
    assert out.exit_code == 0  # the alert has no profile of its own, so the real profile selects it


def test_website_confirmation_is_recorded_and_pinned(world):
    ws, alert, web, calls = world
    result = invoke(ws, "--website", "https://www.cbc-cologne.de", "--job", alert)
    assert result.exit_code == 0 and calls == []
    ctx = RunContext.open(ws)
    emp = ctx.conn.execute("SELECT * FROM employers").fetchone()
    assert (emp["provenance"], emp["pinned"], emp["website"]) == ("user", 1, "https://www.cbc-cologne.de/")
    ctx.close()


def test_a_busy_workspace_is_reported_as_contention(world):
    ws, alert, *_ = world
    with workspace_lock(ws):
        result = invoke(ws, "--discover", "--job", alert)
    assert result.exit_code == 3 and "Another autoapp run is active" in result.output


def test_dry_run_changes_nothing_and_makes_no_calls(world, monkeypatch):
    ws, alert, web, calls = world
    invoke(ws, "--discover")  # one saved employer, one fresh snapshot
    ctx = RunContext.open(ws)
    add_alert(ctx, title="Cloud Platform Engineer (m/w/d)", sid="9000")
    ctx.close()
    web.calls.clear()
    calls.clear()
    monkeypatch.setattr("autoapp.employers.service.make_fetch", lambda ctx: pytest.fail("no page may be requested"))
    monkeypatch.setattr("autoapp.employers.service.ParallelSearch", lambda key: pytest.fail("no search may be made"))
    before = tree(ws.root)
    result = invoke(ws, "--discover", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output and "saved site https://www.cbc-cologne.de/" in result.output
    assert "still fresh" in result.output
    assert tree(ws.root) == before and web.calls == [] and calls == []
    with workspace_lock(ws):  # a dry run does not need the workspace lock
        assert invoke(ws, "--discover", "--dry-run", "--again").exit_code == 0
    assert "would scan it again" in invoke(ws, "--discover", "--dry-run", "--again").output
    assert "would look up the official site" in invoke(ws, "--discover", "--dry-run", "--refresh-site").output


def test_dry_run_reports_missing_key_and_unknown_employers(world, monkeypatch):
    ws, alert, *_ = world
    monkeypatch.delenv("PARALLEL_API_KEY")
    out = invoke(ws, "--discover", "--dry-run").output
    assert "PARALLEL_API_KEY is not set" in out
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    out = invoke(ws, "--discover", "--dry-run").output
    assert "would look up the official site (up to 2 searches" in out and "Searches used: 0 of 1000 today" in out
    assert "would confirm https://www.cbc-cologne.de" in invoke(ws, "--website", "https://www.cbc-cologne.de",
                                                                 "--job", alert, "--dry-run").output


def test_dry_run_with_an_absent_workspace_creates_nothing(tmp_path):
    root = tmp_path / "nowhere"
    result = runner.invoke(app, ["-w", str(root), "employers", "--discover", "--dry-run"])
    assert result.exit_code == 0 and "nothing to discover" in result.output.lower()
    assert not root.exists()
    result = runner.invoke(app, ["-w", str(root), "employers"])
    assert result.exit_code == 0 and "No employer sites saved yet" in result.output and not root.exists()


def test_dry_run_on_an_old_schema_does_not_migrate(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv("PARALLEL_API_KEY", "pk")

    from autoapp.db import schema

    ws = make_workspace(tmp_path, config=EMP_CONFIG)  # setup is not marked complete: a dry run must not care
    raw = sqlite3.connect(ws.db_path)
    for n, step in enumerate(schema.MIGRATIONS[:5]):
        raw.executescript(f"BEGIN;\n{step}\nPRAGMA user_version = {n + 1};\nCOMMIT;")
    raw.execute("INSERT INTO jobs (id, title, company, discovered_at, updated_at, status) VALUES "
                "('j1', 'Data Engineer', 'Acme GmbH', 'x', 'x', 'new')")
    raw.execute("INSERT INTO job_sources (job_id, source, source_job_id, seen_at) VALUES ('j1', 'linkedin-alert', '1', 'x')")
    raw.commit()
    raw.close()
    before = tree(ws.root)
    out = invoke(ws, "--discover", "--dry-run")
    assert out.exit_code == 0 and "Acme GmbH" in out.output and "would look up" in out.output
    assert tree(ws.root) == before
    assert sqlite3.connect(ws.db_path).execute("PRAGMA user_version").fetchone()[0] == 5


def test_documented_options_exist_in_the_cli_help():
    from pathlib import Path

    readme = (Path(__file__).parent.parent.parent / "README.md").read_text(encoding="utf-8")
    result = runner.invoke(app, ["employers", "--help"])
    assert result.exit_code == 0
    for flag in ("--discover", "--job", "--profile", "--again", "--refresh-site", "--website", "--dry-run"):
        assert flag in result.output and flag in readme
    assert "autoapp run --dry-run" in readme and "employers --discover --dry-run" in readme
