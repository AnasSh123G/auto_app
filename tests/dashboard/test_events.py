"""Run events, counters, storage (14.1) and the events the steps emit (14.2)."""

from __future__ import annotations

import json

import pytest

from autoapp.dashboard.wiring import attach_run_events, models_in_use
from autoapp.events import DbEventWriter, EventBus, RunEvent, RunStats, StatsKeeper, format_event
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.steps.pipeline import RunSummary, run_pipeline
from autoapp.steps.prepare import PrepareSummary
from tests.steps.test_pipeline import _ctx, _source
from tests.steps.test_prepare_step import FakeRenderer, factory

ALL_COUNTED = [
    ("source_done", {"found": 5, "new": 4, "duplicates": 1, "filtered": 2}),
    ("details_done", {"fetched": 3, "unavailable": 1}),
    ("job_approved", {}), ("job_maybe", {}), ("job_skipped", {}), ("job_skipped", {}),
    ("folder_saved", {"channel": "email"}), ("folder_saved", {"channel": "employer_form"}),
    ("email_sent", {}), ("email_would_send", {}), ("email_held", {}), ("email_failed", {}),
    ("form_filled", {}), ("needs_review", {}), ("warning", {}), ("error", {}),
    ("judge_reviewed", {"score": 80, "agrees": False}), ("judge_failed", {}),
    ("llm_call", {"tokens": 100, "cost_usd": 0.01}), ("llm_call", {"tokens": 50, "cost_usd": None}),
]


def test_each_kind_updates_its_counter():
    st = RunStats(sent_today_before=2)
    for kind, data in ALL_COUNTED:
        st.apply(RunEvent(kind, data=data))
    assert (st.ads_found, st.ads_new, st.duplicates, st.filtered) == (5, 4, 1, 2)
    assert (st.details_fetched, st.details_unavailable) == (3, 1)
    assert (st.evaluated, st.approved, st.maybe, st.skipped) == (4, 1, 1, 2)
    assert (st.folders_saved, st.email_folders, st.forms_saved) == (2, 1, 1)
    assert (st.emails_sent, st.emails_would_send, st.emails_held, st.emails_failed) == (1, 1, 1, 1)
    assert (st.forms_filled, st.needs_review, st.warnings, st.errors) == (1, 1, 1, 1)
    assert st.llm_calls == 2 and st.tokens == 150 and st.cost_usd == pytest.approx(0.01) and st.cost_unknown
    assert (st.judge_reviewed, st.judge_score, st.judge_disagreed, st.judge_failed) == (1, 80, 1, 1)
    assert st.sent_today == 3 and st.approval_share == 0.25
    st.apply(RunEvent("step_started", detail="evaluate"))
    assert st.step == "evaluate"
    st.apply(RunEvent("run_finished", data={"ok": False}))
    assert st.finished and st.ok is False and st.step is None


def test_stats_round_trip_through_a_dict():
    st = RunStats(run_id="r1", models={"write": "x"}, approved=2, steps_done=["search"])
    assert RunStats.from_dict(json.loads(json.dumps(st.to_dict()))) == st
    assert RunStats.from_dict({"approved": 1, "unknown_future_field": 3}).approved == 1


def test_form_link_outcomes_count_latest_result_per_job():
    st = RunStats()
    for index, status in enumerate(["found"] * 7 + ["needs_browser"] * 3 + ["login_required"]):
        st.apply(RunEvent("form_resolved", job_id=str(index), data={"form_status": status}))
    st.apply(RunEvent("form_resolved", job_id="0", data={"form_status": "found"}))
    assert st.form_outcomes == {"found": 7, "needs_browser": 3, "login_required": 1}
    st.apply(RunEvent("form_resolved", job_id="7", data={"form_status": "found"}))
    assert st.form_outcomes == {"found": 8, "needs_browser": 2, "login_required": 1}
    assert RunStats.from_dict(st.to_dict()).form_outcomes == st.form_outcomes
    summary = RunSummary(prepare=PrepareSummary(prepared=11,
                         form_outcomes={"found": 7, "needs_browser": 3, "login_required": 1}))
    assert "forms: 7 found, 3 need your browser, 1 login required" in summary.lines()


def test_format_event_is_one_plain_line():
    e = RunEvent("email_sent", job_id="a1", company="Muster GmbH", title="Backend Developer", detail="jobs@m.de")
    line = format_event(e)
    assert "SENT" in line and "Muster GmbH, Backend Developer" in line and "→ jobs@m.de" in line
    assert "\n" not in line and "\x1b" not in line


def test_failing_subscriber_is_removed_and_the_rest_still_run():
    bus, seen, fell_back = EventBus(), [], []

    def broken(e):
        raise RuntimeError("draw failed")

    bus.subscribe(broken, on_error=fell_back.append)
    bus.subscribe(seen.append)
    bus.emit(RunEvent("warning"))
    bus.emit(RunEvent("warning"))
    assert len(seen) == 2 and len(fell_back) == 1 and "draw failed" in str(fell_back[0])


def test_events_are_stored_with_their_run_and_progress_is_throttled(tmp_path):
    ctx = _ctx(tmp_path)
    stats = RunStats(run_id=ctx.run_id)
    t = [0.0]
    writer = DbEventWriter(ctx.conn, ctx.run_id, stats, clock=lambda: t[0])
    bus = EventBus()
    bus.subscribe(StatsKeeper(stats))
    bus.subscribe(writer)
    for i in range(5):
        t[0] = i * 0.2  # five events within 0.8 seconds
        bus.emit(RunEvent("job_skipped", job_id=f"j{i}", company="A", title="T"))
    assert writer.progress_writes == 1
    t[0] = 1.5
    bus.emit(RunEvent("job_skipped"))
    assert writer.progress_writes == 2
    bus.emit(RunEvent("step_started", detail="prepare"))  # step changes are always written
    assert writer.progress_writes == 3
    rows = ctx.conn.execute("SELECT run_id, kind, job_id FROM run_events ORDER BY id").fetchall()
    assert len(rows) == 7 and {r["run_id"] for r in rows} == {ctx.run_id}
    progress = json.loads(ctx.conn.execute("SELECT progress FROM runs WHERE id = ?", (ctx.run_id,)).fetchone()[0])
    assert progress["stats"]["skipped"] == 6 and progress["stats"]["step"] == "prepare"
    ctx.close()


def test_llm_calls_are_not_stored_as_feed_rows(tmp_path):
    ctx = _ctx(tmp_path)
    stats = RunStats()
    bus = EventBus()
    bus.subscribe(StatsKeeper(stats))
    bus.subscribe(DbEventWriter(ctx.conn, ctx.run_id, stats))
    bus.emit(RunEvent("llm_call", data={"tokens": 10, "cost_usd": 0.0}))
    assert ctx.conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0
    assert stats.llm_calls == 1
    ctx.close()


def test_models_in_use_names_every_task(tmp_path):
    ctx = _ctx(tmp_path)
    models = models_in_use(ctx.config.llm)
    assert set(models) == {"evaluate", "write", "assist form-fill", "find form", "read alert emails", "judge"}
    ctx.close()


# --- 14.2: what the steps emit ------------------------------------------------------------


def _run(tmp_path, *, dry_run=False):
    ctx = _ctx(tmp_path, dry_run=dry_run)
    attach_run_events(ctx)
    seen = []
    ctx.events.subscribe(seen.append)
    s = run_pipeline(ctx, api_sources=[_source()], mailbox=FakeMailbox([]),
                     renderer_factory=factory(FakeRenderer([1])), mailer=FakeMailer())
    return ctx, s, seen


def _visible(seen):
    return [e.kind for e in seen if e.level != "hidden"]


def test_event_sequence_for_one_approved_email_job(tmp_path):
    ctx, s, seen = _run(tmp_path)
    assert s.ok, s.errors
    kinds = _visible(seen)
    assert kinds == ["source_done", "source_done", "job_approved", "folder_saved", "email_sent"]
    saved = next(e for e in seen if e.kind == "folder_saved")
    sent = next(e for e in seen if e.kind == "email_sent")
    assert saved.company == "Muster GmbH" and saved.detail.startswith("application_") and saved.data["channel"] == "email"
    assert sent.detail == "jobs@muster-gmbh.de" and sent.job_id == saved.job_id
    steps = [e.detail for e in seen if e.kind == "step_started"]
    assert steps == ["search", "fetch", "evaluate", "own ads", "prepare", "send"]
    assert seen[-1].kind == "run_finished"
    assert ctx.stats.folders_saved == 1 and ctx.stats.emails_sent == 1 and ctx.stats.finished
    stored = [r["kind"] for r in ctx.conn.execute("SELECT kind FROM run_events WHERE run_id = ? ORDER BY id",
                                                   (ctx.run_id,))]
    assert [k for k in stored if k not in ("step_started", "step_finished", "run_finished")] == kinds
    ctx.close()


def test_event_sequence_for_a_dry_run(tmp_path):
    ctx, s, seen = _run(tmp_path, dry_run=True)
    kinds = _visible(seen)
    assert kinds[-2:] == ["folder_saved", "email_would_send"] and "email_sent" not in kinds
    assert ctx.stats.dry_run and ctx.stats.emails_sent == 0 and ctx.stats.emails_would_send == 1
    ctx.close()


def test_events_go_to_the_log(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="autoapp.events"):
        ctx, _, _ = _run(tmp_path)
    lines = [r.getMessage() for r in caplog.records if r.name == "autoapp.events"]
    assert any("SAVED" in ln and "Muster GmbH" in ln for ln in lines)
    assert any("SENT" in ln and "jobs@muster-gmbh.de" in ln for ln in lines)
    ctx.close()


def test_held_back_email_is_reported(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.email.min_score = 95
    attach_run_events(ctx)
    run_pipeline(ctx, api_sources=[_source()], mailbox=FakeMailbox([]),
                 renderer_factory=factory(FakeRenderer([1])), mailer=FakeMailer())
    held = [r for r in ctx.conn.execute("SELECT kind, detail FROM run_events WHERE kind = 'email_held'")]
    assert len(held) == 1 and "below email.min_score" in held[0]["detail"]
    ctx.close()


def test_commands_close_their_run(tmp_path):
    from typer.testing import CliRunner

    from autoapp.cli import app
    from tests.helpers import make_workspace

    ws = make_workspace(tmp_path)
    CliRunner().invoke(app, ["-w", str(ws.root), "export"])
    CliRunner().invoke(app, ["-w", str(ws.root), "mark", "nope", "skip"])
    from autoapp.db.schema import open_db

    conn = open_db(ws.db_path)
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE finished_at IS NULL").fetchone()[0] == 0
    conn.close()
