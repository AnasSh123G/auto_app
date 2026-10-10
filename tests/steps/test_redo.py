"""Redo everything that is not sent: jobs go back to new, folders are rebuilt in place, sent jobs stay."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.llm.fake import FakeLLM
from autoapp.steps import redo
from autoapp.steps.context import RunContext
from autoapp.steps.prepare import prepare_jobs
from tests.helpers import CONFIG, make_workspace
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory
from tests.test_cli_commands import SETUP


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def job_in(ctx, status):
    jid = approved_job(ctx, title=f"Role {status}", company=f"Firma {status}")
    ctx.repo.transition(jid, status)
    return jid


def test_plan_redoes_unsent_jobs_and_never_touches_sent_or_unsure_ones(ctx):
    ids = {s: job_in(ctx, s) for s in ("skipped", "review", "approved", "prepared", "sent", "submitted", "rejected",
                                       "drafted", "send_unconfirmed", "filtered", "description_unavailable")}
    p = redo.plan(ctx.conn)
    assert set(p.job_ids) == {ids[s] for s in ("skipped", "review", "approved", "prepared", "description_unavailable")}
    assert p.kept == {"drafted": 1, "send_unconfirmed": 1}
    text = " ".join(redo.describe(p))
    assert "5 jobs would be processed again" in text and "drafted" in text and "sent, submitted" in text


def test_redo_resets_jobs_but_keeps_sent_ones_and_records_why(ctx):
    done, sent = job_in(ctx, "prepared"), job_in(ctx, "sent")
    ctx.repo.upsert_application(done, form_status="partial", form_note="x", form_entry_url="https://e.example/")
    ctx.repo.set_needs_review(done, ["something"])
    redo.redo_unsent(ctx)
    assert ctx.repo.get_job(done)["status"] == "new" and ctx.repo.get_job(sent)["status"] == "sent"
    application = ctx.repo.get_application(done)
    assert application["form_status"] is None and application["form_entry_url"] is None and application["prepared_at"] is None
    assert ctx.repo.get_job(done)["needs_review"] == 0
    assert ctx.repo.history(done)[-1]["note"] == "redo: processed again"
    assert not ctx.repo.history(sent)[-1]["note"] or "redo" not in ctx.repo.history(sent)[-1]["note"]


def test_backup_is_a_readable_copy(ctx):
    job_in(ctx, "prepared")
    path = Path(redo.backup_database(ctx))
    assert path.parent.parent == ctx.ws.backups_dir and path.parent.name.startswith("redo-")
    import sqlite3

    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_a_redone_folder_is_rebuilt_in_place_and_the_old_letter_is_kept(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])), today=date(2026, 10, 7))
    folder = Path(ctx.repo.get_application(jid)["folder"])
    (folder / "extra_note.txt").write_text("mine")
    (folder / "cover_letter.md").write_text("my edited letter")
    # without a redo a finished folder is left alone
    assert prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1]))).prepared == 0
    redo.redo_unsent(ctx)
    ctx.repo.transition(jid, "approved")  # the evaluation step would do this
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    summary = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])), today=date(2026, 10, 8))
    assert summary.prepared == 1 and Path(ctx.repo.get_application(jid)["folder"]) == folder
    assert [p.name for p in ctx.ws.applications_dir.iterdir()] == [folder.name]  # no second folder
    assert (folder / "extra_note.txt").read_text() == "mine"
    backups = list(folder.glob("cover_letter.*.md"))
    assert len(backups) == 1 and backups[0].read_text() == "my edited letter"
    assert ctx.repo.get_job(jid)["status"] == "prepared"


def run_cli(tmp_path, monkeypatch, *args, input=None):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    ws = make_workspace(tmp_path, config=CONFIG + SETUP)
    return ws, CliRunner().invoke(app, ["-w", str(ws.root), "run", *args], input=input)


def test_redo_cannot_be_combined_with_fresh(tmp_path, monkeypatch):
    ws, result = run_cli(tmp_path, monkeypatch, "--redo", "--fresh", "--non-interactive")
    assert result.exit_code == 2 and "cannot be combined" in result.output


def test_dry_run_only_describes_and_the_prompt_default_is_no(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    ws = make_workspace(tmp_path, config=CONFIG + SETUP)
    c = RunContext.open(ws)
    jid = job_in(c, "skipped")
    c.close()
    result = CliRunner().invoke(app, ["-w", str(ws.root), "run", "--redo", "--dry-run", "--non-interactive"])
    assert result.exit_code == 0 and "1 jobs would be processed again" in result.output
    assert "nothing was reset" in result.output
    c = RunContext.open(ws)
    assert c.repo.get_job(jid)["status"] == "skipped" and not list(ws.backups_dir.glob("redo-*"))
    c.close()


def test_declining_the_question_changes_nothing_and_accepting_backs_up_first(tmp_path, monkeypatch, capsys):
    from autoapp.commands import _start_redo
    from autoapp.setup.prompts import ScriptedPrompter

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    ctx = RunContext.open(make_workspace(tmp_path, config=CONFIG + SETUP))
    jid = job_in(ctx, "skipped")
    assert _start_redo(ctx, dry_run=False, interactive=True, prompter=ScriptedPrompter([("Process these 1 jobs", False)])) is False
    assert ctx.repo.get_job(jid)["status"] == "skipped" and not list(ctx.ws.backups_dir.glob("redo-*"))
    assert _start_redo(ctx, dry_run=False, interactive=True, prompter=ScriptedPrompter([("Process these 1 jobs", True)])) is True
    assert ctx.repo.get_job(jid)["status"] == "new" and len(list(ctx.ws.backups_dir.glob("redo-*"))) == 1
    assert "Database backed up" in capsys.readouterr().out
    ctx.close()
