"""``autoapp reset-data`` and Settings > Delete all job data: job data goes, the setup stays, only after a yes."""

from __future__ import annotations

from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.steps.context import RunContext
from tests.helpers import make_workspace


def _filled(tmp_path, config=None):
    ws = make_workspace(tmp_path, **({"config": config} if config else {}))
    ctx = RunContext.open(ws)
    ctx.repo.insert_job(title="Dev", company="A GmbH", job_url="https://a.example/1")
    ctx.close()
    (ws.applications_dir / "application_a_dev_x").mkdir(parents=True)
    (ws.applications_dir / "application_a_dev_x" / "letter.md").write_text("Hallo")
    ws.csv_path.write_text("id;company\n")
    (ws.logs_dir / "run-1.log").write_text("log")
    (ws.root / "test-runs" / "20261010-120000").mkdir(parents=True)
    ws.form_answers_path.write_text("## Anrede\nHerr\n")
    (ws.browser_profile_dir / "Default").mkdir(parents=True)
    return ws


def _setup_files(ws):
    return [ws.config_path, ws.profile_path, ws.writing_instructions_path, ws.documents_dir / "Lebenslauf_de.pdf"]


def test_nothing_is_deleted_without_a_yes(tmp_path, monkeypatch):
    ws = _filled(tmp_path)
    r = CliRunner().invoke(app, ["-w", str(ws.root), "reset-data"])  # no terminal and no --yes
    assert r.exit_code == 2 and "nothing deleted" in r.output and ws.db_path.exists()
    monkeypatch.setattr("autoapp.setup.prompts.is_interactive", lambda: True)
    r = CliRunner().invoke(app, ["-w", str(ws.root), "reset-data"], input="n\n")
    assert r.exit_code == 0 and "Nothing was deleted" in r.output
    assert "autoapp.db: the job history (1 jobs" in r.output and "config.yaml: settings" in r.output
    assert ws.db_path.exists() and ws.csv_path.exists() and any(ws.applications_dir.iterdir())


def test_yes_deletes_the_job_data_and_keeps_the_setup(tmp_path, monkeypatch):
    ws = _filled(tmp_path)
    monkeypatch.setattr("autoapp.setup.prompts.is_interactive", lambda: True)
    r = CliRunner().invoke(app, ["-w", str(ws.root), "reset-data"], input="y\n")
    assert r.exit_code == 0 and "Deleted." in r.output, r.output
    assert not ws.db_path.exists() and not ws.csv_path.exists() and not (ws.root / "test-runs").exists()
    assert list(ws.applications_dir.iterdir()) == [] and not (ws.logs_dir / "run-1.log").exists()
    assert all(p.exists() for p in _setup_files(ws))
    assert ws.form_answers_path.exists() and (ws.browser_profile_dir / "Default").exists()  # kept unless asked
    ctx = RunContext.open(ws)  # the app starts again from an empty history
    assert ctx.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    ctx.close()


def test_answers_and_logins_go_only_when_asked_and_yes_skips_the_question(tmp_path):
    ws = _filled(tmp_path)
    r = CliRunner().invoke(app, ["-w", str(ws.root), "reset-data", "--yes", "--answers", "--logins"])
    assert r.exit_code == 0, r.output
    assert not ws.form_answers_path.exists() and not ws.browser_profile_dir.exists()
    assert all(p.exists() for p in _setup_files(ws))
    again = CliRunner().invoke(app, ["-w", str(ws.root), "reset-data", "--yes"])
    assert "no job data to delete" in again.output


def test_the_settings_menu_asks_and_deletes(tmp_path, monkeypatch):
    from tests.helpers import CONFIG
    from tests.menu.test_menu import SETUP, open_menu

    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    ws = _filled(tmp_path, CONFIG + SETUP)
    r, p = open_menu(ws, [("What would you like", "settings"), ("Settings", "wipe"),
                          ("saved form answers", False), ("browser logins", False), ("cannot be undone", False),
                          ("What would you like", "settings"), ("Settings", "wipe"),
                          ("saved form answers", True), ("browser logins", False), ("cannot be undone", True),
                          ("What would you like", "quit")])
    assert p.remaining == 0
    assert not ws.form_answers_path.exists() and ws.browser_profile_dir.exists()
    assert all(path.exists() for path in _setup_files(ws)) and not any(ws.applications_dir.iterdir())
    ctx = RunContext.open(ws, start_run=False)  # the menu reopened an empty history to draw its next screen
    assert ctx.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    ctx.close()
