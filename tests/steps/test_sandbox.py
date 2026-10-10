"""Fresh test runs: every job is new in a separate test workspace; the real one is not touched."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.employers.repo import EmployerRepo
from autoapp.steps import sandbox
from tests.helpers import CONFIG, make_workspace
from tests.test_cli_commands import SETUP


def seeded_real(tmp_path, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    ws = make_workspace(tmp_path, config=CONFIG + SETUP)
    conn = open_db(ws.db_path)
    repo = Repo(conn)
    repo.insert_job(title="Data Engineer", company="Acme", status="skipped", job_url="https://ad.example/1",
                    canonical_url="https://ad.example/1")
    conn.execute("INSERT INTO processed_emails (message_id, sender, processed_at) VALUES ('<m1>', 'x', '2026-10-09')")
    conn.execute("INSERT INTO api_usage (source, day, requests) VALUES ('adzuna', '2026-10-09', 40)")
    EmployerRepo(conn).create_employer("Acme GmbH", status="verified", website="https://acme.example/", provenance="search")
    conn.commit()
    conn.close()
    return ws


def test_the_test_workspace_has_no_history_but_keeps_sites_limits_and_settings(tmp_path):
    real = seeded_real(tmp_path)
    box = sandbox.create(real)
    assert box.ws.root.parent == real.root / "test-runs"
    conn = open_db(box.ws.db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == 0  # alert emails are read again
    assert conn.execute("SELECT website FROM employers").fetchone()[0] == "https://acme.example/"  # no repeated lookups
    assert conn.execute("SELECT requests FROM api_usage").fetchone()[0] == 40  # provider limits still hold
    conn.close()
    assert (box.ws.config_path.read_text() == real.config_path.read_text()
            and box.ws.profile_path.read_text() == real.profile_path.read_text())
    assert sorted(p.name for p in box.ws.documents_dir.iterdir()) == sorted(p.name for p in real.documents_dir.iterdir())
    assert not box.ws.env_path.exists()  # secrets are not copied
    assert sandbox.create(real).ws.root != box.ws.root  # two runs in the same second get two folders


def test_usage_is_merged_back_and_the_real_history_is_untouched(tmp_path):
    real = seeded_real(tmp_path)
    box = sandbox.create(real)
    conn = open_db(box.ws.db_path)
    Repo(conn).insert_job(title="Python Dev", company="New", status="prepared", job_url="https://ad.example/2",
                          canonical_url="https://ad.example/2")
    conn.execute("UPDATE api_usage SET requests = 43")
    conn.execute("INSERT INTO api_usage (source, day, requests) VALUES ('parallel-search', '2026-10-09', 2)")
    conn.execute("INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, created_at) "
                 "VALUES ('openrouter', 'x/y', 'evaluate', 100, 10, '2026-10-09T12:00:00+00:00')")
    conn.commit()
    conn.close()
    sandbox.merge_back(box)
    conn = open_db(real.db_path)
    usage = dict(conn.execute("SELECT source, requests FROM api_usage").fetchall())
    assert usage == {"adzuna": 43, "parallel-search": 2}
    assert conn.execute("SELECT model, input_tokens FROM llm_usage").fetchall()[0][:] == ("x/y", 100)
    assert [r[0] for r in conn.execute("SELECT title FROM jobs")] == ["Data Engineer"]  # the test job stays in the test workspace
    conn.close()


def test_fresh_is_only_for_test_runs(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["-w", str(real.root), "run", "--mode", "automatic", "--fresh", "--non-interactive"])
    assert result.exit_code == 2 and "only for test runs" in result.output
    assert not (real.root / "test-runs").exists()


def test_fresh_run_works_in_the_test_workspace_and_restores_the_real_one(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    seen = {}

    def fake_pipeline(ctx, profile):
        seen["root"], seen["mode"] = ctx.ws.root, ctx.mode
        seen["jobs"] = ctx.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return SimpleNamespace(lines=lambda: ["done"], forms_to_finish=[], ok=True)

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", fake_pipeline)
    result = CliRunner().invoke(app, ["-w", str(real.root), "run", "--fresh", "--non-interactive"])
    assert result.exit_code == 0, result.output
    assert seen["mode"] == "test" and seen["jobs"] == 0 and seen["root"].parent == real.root / "test-runs"
    assert "Fresh test run" in result.output and str(seen["root"]) in " ".join(result.output.split())
    conn = open_db(real.db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    conn.close()


def test_a_test_run_folder_uses_the_real_workspaces_keys_when_opened_later(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    real = seeded_real(tmp_path, monkeypatch)
    real.env_path.write_text("OPENROUTER_API_KEY=sk-real-key\n")
    box = sandbox.create(real)
    assert not box.ws.env_path.exists()  # no secrets are duplicated
    box.ws.load_env()  # what `autoapp -w workspace/test-runs/<time>` does at start
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-real-key"
