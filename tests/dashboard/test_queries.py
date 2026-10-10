"""Statistics for a period (14.3), on a seeded database."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from autoapp.dashboard.queries import PERIODS, compute_stats, held_reason, period_window
from autoapp.llm.cost import PriceTable
from autoapp.steps.context import RunContext
from tests.helpers import make_workspace

NOW = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)


def iso(dt):
    return dt.isoformat(timespec="seconds")


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path), start_run=False)
    yield c
    c.close()


def _job(conn, jid, at, status="new", **kw):
    conn.execute("INSERT INTO jobs (id, title, company, status, discovered_at, updated_at, needs_review) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (jid, f"Job {jid}", "ACME", status, iso(at), iso(at),
                                                   kw.get("needs_review", 0)))
    conn.execute("INSERT INTO job_sources (job_id, source, source_job_id, seen_at) VALUES (?, ?, ?, ?)",
                 (jid, kw.get("source", "adzuna"), jid, iso(at)))


def _eval(conn, jid, at, score, decision, reqs=()):
    payload = {"result": {"requirements": [{"text": t, "status": s} for t, s in reqs]}}
    conn.execute("INSERT INTO evaluations (job_id, created_at, fit_score, decision, payload) VALUES (?, ?, ?, ?, ?)",
                 (jid, iso(at), score, decision, json.dumps(payload)))


def seed(conn):
    old, recent, today, run_start = (NOW - timedelta(days=20), NOW - timedelta(days=3), NOW - timedelta(hours=2),
                                     NOW - timedelta(minutes=30))
    # A run 30 minutes ago with events, and an older run.
    conn.execute("INSERT INTO runs (id, started_at, finished_at) VALUES ('old', ?, ?)",
                 (iso(old), iso(old + timedelta(minutes=5))))
    conn.execute("INSERT INTO runs (id, started_at, finished_at) VALUES ('r1', ?, ?)",
                 (iso(run_start), iso(NOW - timedelta(minutes=10))))
    _job(conn, "o1", old, "skipped")
    _eval(conn, "o1", old, 30, "skip", [("Kubernetes", "missing")])
    _job(conn, "w1", recent, "review", source="arbeitsagentur")
    _eval(conn, "w1", recent, 65, "maybe", [("Kubernetes", "missing"), ("Docker", "bridgeable")])
    for jid, score, decision in (("t1", 90, "apply"), ("t2", 80, "apply"), ("t3", 40, "skip"), ("t4", 70, "maybe")):
        _job(conn, jid, run_start + timedelta(minutes=1), "prepared" if decision == "apply" else "skipped")
        _eval(conn, jid, run_start + timedelta(minutes=2), score, decision,
              [("Kubernetes", "missing"), ("Terraform", "missing"), ("Docker", "bridgeable")])
    conn.execute("INSERT INTO applications (job_id, channel, prepared_at) VALUES ('t1', 'email', ?)",
                 (iso(run_start + timedelta(minutes=3)),))
    conn.execute("INSERT INTO applications (job_id, channel, prepared_at) VALUES ('t2', 'employer_form', ?)",
                 (iso(run_start + timedelta(minutes=3)),))
    conn.execute("INSERT INTO send_attempts (job_id, message_id, state, created_at, updated_at, recipient) "
                 "VALUES ('t1', '<m>', 'sent', ?, ?, 'jobs@acme.de')", (iso(run_start + timedelta(minutes=4)),) * 2)
    conn.execute("INSERT INTO status_events (job_id, to_status, at) VALUES ('t3', 'filtered', ?)", (iso(today),))
    conn.execute("INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, created_at, run_id) "
                 "VALUES ('anthropic', 'claude-sonnet-5-5', 'evaluate', 1000000, 100000, ?, 'r1')",
                 (iso(run_start + timedelta(minutes=2)),))
    events = [
        ("source_done", None, '{"source": "adzuna", "found": 10, "new": 4, "duplicates": 6, "filtered": 1}'),
        ("source_done", None, '{"source": "alerts", "found": 3, "new": 0, "duplicates": 3, "unrecognised": 2}'),
        ("email_held", "fit score 80 is below email.min_score 85", None),
        ("email_held", "daily cap of 5 reached", None),
        ("form_filled", "12 fields filled", None),
        ("warning", "adzuna: daily request limit reached (150)", None),
    ]
    for kind, detail, data in events:
        conn.execute("INSERT INTO run_events (run_id, at, kind, detail, data) VALUES ('r1', ?, ?, ?, ?)",
                     (iso(run_start + timedelta(minutes=5)), kind, detail, data))
    conn.execute("INSERT INTO api_usage (source, day, requests) VALUES ('adzuna', '2026-10-08', 12)")
    conn.execute("INSERT INTO api_usage (source, day, requests) VALUES ('adzuna', '2026-10-01', 30)")
    conn.commit()


def stats(ctx, period):
    return compute_stats(ctx.conn, ctx.config, PriceTable.load({}), period, now=NOW, models={"write": "m"})


def test_latest_run(ctx):
    seed(ctx.conn)
    s = stats(ctx, "run")
    assert s.run_id == "r1" and s.label == "latest run"
    assert (s.ads_found, s.duplicates, s.unrecognised_senders) == (13, 9, 2)
    assert s.sources["adzuna"].found == 10 and s.sources["alerts"].found == 3
    assert (s.evaluated, s.approved, s.maybe, s.skipped) == (4, 2, 1, 1)
    assert s.approval_share == 0.5 and s.average_score == 70
    assert s.folders_by_channel == {"email": 1, "employer_form": 1} and s.folders_saved == 2
    assert s.emails_sent == 1 and s.emails_held == {"minimum score": 1, "cap": 1} and s.forms_filled == 1
    assert s.llm_calls == 1 and s.cost.total == pytest.approx(3.0)  # 1M in * $2 + 0.1M out * $10
    assert s.cost_per_application == pytest.approx(1.5)
    assert s.top_missing[0] == ("kubernetes", 4) and ("terraform", 4) in s.top_missing
    assert s.top_bridgeable == [("docker", 4)]
    assert s.last_run_problems == [("warning", "adzuna: daily request limit reached (150)")]
    assert s.adzuna == {"today": (12, 150), "this month": (42, 900)}
    assert s.models == {"write": "m"}


def test_periods_recompute(ctx):
    seed(ctx.conn)
    today, week, month, every = (stats(ctx, p) for p in ("today", "7d", "30d", "all"))
    assert today.evaluated == 4 and week.evaluated == 5 and month.evaluated == 6 and every.evaluated == 6
    assert today.filtered == 1 and week.top_missing[0] == ("kubernetes", 5)
    assert (today.label, week.label, month.label, every.label) == ("today", "last 7 days", "last 30 days", "all time")
    assert every.status_counts == {"skipped": 3, "prepared": 2, "review": 1} and every.review_queue == 1


def test_statistics_keep_latest_discovery_per_job(ctx):
    seed(ctx.conn)
    for jid, status in (("t2", "partial"), ("t2", "found"), ("t1", "login_required")):
        ctx.conn.execute("INSERT INTO run_events (run_id,at,kind,job_id,data) VALUES ('r1',?,'form_resolved',?,?)",
                         (iso(NOW - timedelta(minutes=20)), jid, json.dumps({"form_status": status})))
    ctx.conn.commit()
    assert stats(ctx, "run").form_outcomes == {"found": 1, "login_required": 1}


def test_history_without_events_uses_the_tables(ctx):
    _job(ctx.conn, "a", NOW - timedelta(days=1))
    _job(ctx.conn, "b", NOW - timedelta(days=1), source="arbeitsagentur")
    ctx.conn.commit()
    s = stats(ctx, "7d")
    assert s.ads_found == 2 and s.ads_new == 2 and s.duplicates == 0
    assert {k: (v.found, v.new) for k, v in s.sources.items()} == {"adzuna": (1, 1), "arbeitsagentur": (1, 1)}


def test_empty_database_is_all_zero(ctx):
    for period in PERIODS:
        s = stats(ctx, period)
        assert not s.has_runs and s.ads_found == 0 and s.evaluated == 0 and s.folders_saved == 0
        assert s.approval_share is None and s.average_score is None and s.cost_per_application is None


def test_unpriced_model_is_unknown_not_zero(ctx):
    ctx.conn.execute("INSERT INTO runs (id, started_at) VALUES ('r', ?)", (iso(NOW - timedelta(minutes=1)),))
    ctx.conn.execute("INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, created_at, run_id) "
                     "VALUES ('openrouter', 'qwen/unpriced', 'evaluate', 10, 10, ?, 'r')", (iso(NOW),))
    ctx.conn.commit()
    s = stats(ctx, "run")
    assert s.cost.total is None and "qwen/unpriced" in s.cost.unknown_models


def test_unknown_period_is_rejected(ctx):
    with pytest.raises(ValueError, match="valid periods"):
        period_window(ctx.conn, "week")


def test_held_reason_labels():
    assert held_reason("recipient a@gmail.com is a free-mail address; check it by hand") == "free-mail address"
    assert held_reason("an application to X was sent 3 days ago (cooldown 14 days)") == "company cooldown"
    assert held_reason("something new") == "other"
