"""A broken document setup must fail before consuming alerts or contacting services."""

import json
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter

from autoapp.events import DbEventWriter, RunStats, StatsKeeper
from autoapp.steps import pipeline
from autoapp.steps.context import RunContext
from tests.helpers import CV_TEXT, make_workspace


@pytest.fixture
def isolated_steps(monkeypatch):
    names = (
        "sync_csv", "check_drafts", "run_search", "find_alert_ads", "run_discovery", "fetch_details",
        "evaluate_jobs", "select_sample_jobs", "find_own_ads", "seed_arbeitsagentur_misses", "prepare_jobs",
        "send_applications", "draft_applications", "send_test_applications", "export_csv",
    )
    steps = {name: Mock(return_value=None) for name in names}
    for name, step in steps.items():
        monkeypatch.setattr(pipeline, name, step)
    return steps


def _observe(ctx):
    events = []
    stats = RunStats(run_id=ctx.run_id)
    ctx.events.subscribe(StatsKeeper(stats))
    ctx.events.subscribe(DbEventWriter(ctx.conn, ctx.run_id, stats))
    ctx.events.subscribe(events.append)
    return events, stats


@pytest.mark.parametrize("source_mode", ["email_only", "full", "web_only"])
@pytest.mark.parametrize("mode", ["automatic", "review", "test"])
@pytest.mark.parametrize("missing", ["Lebenslauf_de.pdf", "Zeugnis_Muster.pdf"])
def test_missing_declared_document_fails_before_any_step(tmp_path, isolated_steps, monkeypatch,
                                                       source_mode, mode, missing):
    ws = make_workspace(tmp_path)
    (ws.documents_dir / missing).unlink()
    ctx = RunContext.open(ws, mode=mode)
    ctx.source_mode = source_mode
    llm = Mock(side_effect=AssertionError("must not build a model client"))
    monkeypatch.setattr(ctx, "llm", llm)
    events, stats = _observe(ctx)
    try:
        result = pipeline.run_pipeline(ctx)
        assert not result.ok
        assert len(result.errors) == 1
        error = result.errors[0]
        assert "documents" in error and missing in error
        assert "setup" in error.lower() and "documents" in error.lower()
        assert str(ws.profile_path) in error and str(ws.documents_dir) in error
        assert result.source_mode == source_mode and result.mode == mode
        assert result.search is None and result.evaluate is None and result.prepare is None
        assert result.drafts is None and result.send is None and result.test is None
        for step in isolated_steps.values():
            step.assert_not_called()
        llm.assert_not_called()
        assert ctx._http is None and ctx._apply_links is None
        assert ctx.conn.execute("SELECT count(*) FROM processed_emails").fetchone()[0] == 0
        assert ctx.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert ctx.conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
        assert not ws.csv_path.exists()
        assert list(ws.applications_dir.iterdir()) == []
        row = ctx.conn.execute("SELECT finished_at, summary FROM runs WHERE id = ?", (ctx.run_id,)).fetchone()
        assert row["finished_at"]
        assert json.loads(row["summary"])["ok"] is False
        assert json.loads(row["summary"])["lines"] == result.lines()
        assert [e.kind for e in events] == ["error", "run_finished"]
        assert stats.finished and stats.ok is False and stats.errors == 1
        assert ctx.conn.execute("SELECT count(*) FROM run_events WHERE kind = 'run_finished'").fetchone()[0] == 1
    finally:
        ctx.close()


@pytest.mark.parametrize("broken_cv", ["image_only", "corrupt"])
def test_unreadable_cv_stops_before_discovery(tmp_path, isolated_steps, broken_cv):
    ws = make_workspace(tmp_path)
    path = ws.documents_dir / "Lebenslauf_de.pdf"
    if broken_cv == "image_only":
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(path)
    else:
        path.write_bytes(b"not a PDF")
    ctx = RunContext.open(ws)
    try:
        result = pipeline.run_pipeline(ctx)
        assert not result.ok
        reason = "no extractable text" if broken_cv == "image_only" else "cannot read"
        assert reason in result.errors[0]
        assert "setup" in result.errors[0].lower()
        for step in isolated_steps.values():
            step.assert_not_called()
    finally:
        ctx.close()


@pytest.mark.parametrize("source_mode", ["email_only", "full", "web_only"])
def test_corrected_profile_uses_current_cv_and_continues_pipeline(tmp_path, isolated_steps, source_mode):
    ws = make_workspace(tmp_path)
    current_cv = "Current CV.pdf"
    (ws.documents_dir / "Lebenslauf_de.pdf").rename(ws.documents_dir / current_cv)
    ws.profile_path.write_text(ws.profile_path.read_text().replace("Lebenslauf_de.pdf", current_cv))
    ctx = RunContext.open(ws, mode="review")
    ctx.source_mode = source_mode
    try:
        result = pipeline.run_pipeline(ctx)
        assert result.ok, result.errors
        assert result.source_mode == source_mode
        assert ctx.documents.cv.file == current_cv
        assert "Python" in ctx.documents.cv_text() and CV_TEXT.split(".")[0] in ctx.documents.cv_text()
        isolated_steps["run_search"].assert_called_once()
        isolated_steps["fetch_details"].assert_called_once()
        isolated_steps["evaluate_jobs"].assert_called_once()
        isolated_steps["prepare_jobs"].assert_called_once()
        isolated_steps["draft_applications"].assert_called_once()
        isolated_steps["export_csv"].assert_called_once()
        assert ctx.documents is ctx.documents
    finally:
        ctx.close()
