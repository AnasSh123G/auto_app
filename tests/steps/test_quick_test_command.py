"""`autoapp test --quick`: a quick test in a throwaway workspace, ending with a checklist (run-modes spec)."""

from __future__ import annotations

from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.db.schema import open_db
from tests.steps.test_quicktest_report import healthy
from tests.steps.test_sandbox import seeded_real


def invoke(real, *args):
    return CliRunner().invoke(app, ["-w", str(real.root), "test", *args])


def invoke_quick(real, *args):
    return invoke(real, "--quick", *args)


def test_the_quick_test_samples_in_a_test_workspace_and_leaves_the_real_one_alone(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    seen = {}

    def fake_pipeline(ctx, profile=None, **kw):
        seen.update(root=ctx.ws.root, mode=ctx.mode, sample=ctx.sample, jobs=ctx.conn.execute(
            "SELECT COUNT(*) FROM jobs").fetchone()[0], evaluate_limit=ctx.config.limits.max_evaluate_per_run,
            prepare_limit=ctx.config.limits.max_prepare_per_run)
        ctx.conn.execute("UPDATE api_usage SET requests = 43")  # spent in the test run
        ctx.conn.commit()
        return healthy()

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", fake_pipeline)
    result = invoke_quick(real)
    assert result.exit_code == 0, result.output
    assert seen["mode"] == "test" and seen["jobs"] == 0 and seen["root"].parent == real.root / "test-runs"
    assert (seen["sample"].alert_emails, seen["evaluate_limit"], seen["prepare_limit"]) == (2, 6, 4)
    assert "quick test checklist:" in result.output and "OK       alert mailbox" in result.output
    assert "SKIPPED  source arbeitnow: disabled" in result.output
    assert result.output.index("existing form links (real workspace, 0 prepared):") > result.output.index("quick test:")
    conn = open_db(real.db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1  # the real history is untouched
    assert [r[0] for r in conn.execute("SELECT message_id FROM processed_emails")] == ["<m1>"]
    assert conn.execute("SELECT requests FROM api_usage").fetchone()[0] == 43  # but what was spent counts
    conn.close()


def test_a_failed_part_makes_the_command_exit_with_one(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)

    def failing(ctx, profile=None, **kw):
        summary = healthy()
        summary.search.sources["adzuna"].note = "error: Adzuna rejected the app ID or key (HTTP 401)"
        return summary

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", failing)
    result = invoke_quick(real)
    assert result.exit_code == 1
    assert "FAILED   source adzuna" in result.output and "1 FAILED" in result.output
    assert "existing form links (real workspace" in result.output  # the audit is printed after a failed checklist too


def test_a_normal_run_never_prints_a_checklist_or_samples(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    seen = {}

    def fake_pipeline(ctx, profile=None, **kw):
        seen["sample"] = ctx.sample
        return healthy()

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", fake_pipeline)
    result = CliRunner().invoke(app, ["-w", str(real.root), "run", "--mode", "review", "--non-interactive"])
    assert seen["sample"] is None and "quick test checklist" not in result.output


def test_bare_test_prints_the_choices_and_starts_nothing(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda *a, **kw: started.append(kw))
    result = invoke(real)
    assert result.exit_code == 2 and started == []
    assert "--quick" in result.output and "--full" in result.output


def test_both_flags_together_are_an_error(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda *a, **kw: started.append(kw))
    result = invoke(real, "--quick", "--full")
    assert result.exit_code == 2 and started == []


def test_full_is_a_fresh_test_run_without_sampling(tmp_path, monkeypatch):
    real = seeded_real(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda tctx, **kw: calls.append(kw))
    result = invoke(real, "--full")
    assert result.exit_code == 0, result.output
    assert [(c["mode"], c["fresh"], c["sample"], c["ask_mode"], c["dry_run"]) for c in calls] == [
        ("test", True, False, False, False)]
