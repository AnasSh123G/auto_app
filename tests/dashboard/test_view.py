"""Statistics page and live view rendering (14.4, 14.5) at fixed widths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autoapp.dashboard.queries import compute_stats
from autoapp.dashboard.view import feed_line, render_run_top, render_run_view, render_stats_page, to_text
from autoapp.events import RunEvent, RunStats
from autoapp.llm.cost import PriceTable
from autoapp.llm.openrouter_prices import parse_models
from autoapp.steps.context import RunContext
from tests.dashboard.test_queries import NOW, seed
from tests.helpers import make_workspace


@pytest.fixture
def seeded(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path), start_run=False)
    seed(ctx.conn)
    yield ctx
    ctx.close()


def page(ctx, period, width):
    st = compute_stats(ctx.conn, ctx.config, PriceTable.load({}), period, now=NOW,
                       models={"evaluate": "openrouter qwen/qwen3.8-flash (low)", "write": "anthropic claude"})
    return to_text(render_stats_page(st, width, interactive=True), width)


@pytest.mark.parametrize("width", [60, 100, 140])
def test_stats_page_labels_and_numbers(seeded, width):
    text = page(seeded, "run", width)
    for label in ("Statistics: latest run", "Models in use", "approved (high match)", "folders saved",
                  "emails sent", "emails held back", "forms filled", "review queue", "Adzuna today",
                  "estimated cost", "To brush up", "kubernetes"):
        assert label in text, (width, label)
    assert "2 (50 % of evaluated)" in text and "$3.0000" in text and "12 of 150 requests" in text
    assert max(len(line) for line in text.splitlines()) <= width


def test_period_label_changes(seeded):
    assert "Statistics: last 7 days" in page(seeded, "7d", 120)


def test_arbeitnow_feed_details_and_request_caps(seeded):
    seeded.config.sources.arbeitnow.enabled = True
    seeded.conn.execute(
        "INSERT INTO run_events(run_id,at,kind,data) VALUES ('r1',?,'source_done',?)",
        (NOW.isoformat(), '{"source":"arbeitnow","found":2,"new":1,"pages":1,"foreign":3,"uncertain":1}'),
    )
    seeded.conn.execute("INSERT INTO api_usage VALUES ('arbeitnow',?,2)", (NOW.date().isoformat(),))
    seeded.conn.commit()
    text = page(seeded, "run", 140)
    assert "Arbeitnow today" in text and "2 of 200 requests" in text
    assert "foreign: 3" in text and "pages: 1" in text and "uncertain: 1" in text


def test_compact_layout_below_100_columns(seeded):
    narrow = page(seeded, "run", 60)
    wide = page(seeded, "run", 140)
    assert "╭" not in narrow and "╭" in wide  # panels only when there is room


def test_empty_workspace_hint(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path), start_run=False)
    st = compute_stats(ctx.conn, ctx.config, PriceTable.load({}), "today", models={"write": "x"})
    text = to_text(render_stats_page(st, 100), 100)
    assert "No runs yet" in text and "autoapp run" in text and "write" in text
    ctx.close()


def test_unknown_cost_is_not_zero(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path), start_run=False)
    ctx.conn.execute("INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, created_at) "
                     "VALUES ('openrouter', 'x/unpriced', 'evaluate', 5, 5, ?)", (datetime.now(UTC).isoformat(),))
    ctx.conn.commit()
    st = compute_stats(ctx.conn, ctx.config, PriceTable.load({}), "today")
    text = to_text(render_stats_page(st, 100), 100)
    assert "unknown" in text and "$0.0000" not in text.split("estimated cost")[1].splitlines()[0]
    ctx.close()


# --- live view ---------------------------------------------------------------------------

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _ev(kind, n, **kw):
    return RunEvent(kind, at=T0 + timedelta(seconds=n), company=kw.get("company", "ACME GmbH"),
                    title=kw.get("title", "Data Engineer"), detail=kw.get("detail"), data=kw.get("data", {}))


def scripted(dry_run=False):
    rs = RunStats(run_id="r", dry_run=dry_run, started_at=T0.isoformat(), daily_cap=5, sent_today_before=1,
                  models={"evaluate": "qwen", "write": "sonnet"})
    events = [
        RunEvent("step_started", detail="evaluate", at=T0),
        _ev("job_approved", 1, detail="score 88"),
        _ev("job_skipped", 2, company="Other AG", detail="score 30"),
        _ev("folder_saved", 3, detail="application_acme_data-engineer_1", data={"channel": "email"}),
        _ev("email_would_send" if dry_run else "email_sent", 4, detail="jobs@acme.de"),
        _ev("folder_saved", 5, company="Form GmbH", detail="application_form_x_2", data={"channel": "employer_form"}),
        _ev("llm_call", 6, data={"tokens": 1000, "cost_usd": 0.02}),
    ]
    for e in events:
        rs.apply(e)
    return rs, events


@pytest.mark.parametrize("width", [60, 120])
def test_top_counters_and_feed_order(width):
    rs, events = scripted()
    text = to_text(render_run_view(rs, events, width, 40, now=T0 + timedelta(seconds=75)), width)
    assert "step: evaluate" in text and "elapsed 1:15" in text
    for k in ("approved", "folders saved", "emails sent", "forms saved", "sent today", "est. cost so far"):
        assert k in text, k
    assert "2 of 5" in text  # 1 sent before + 1 in this run, against the cap
    lines = [ln for ln in text.splitlines() if any(lbl in ln for lbl in ("APPROVED", "SKIPPED", "SAVED", "SENT"))]
    order = [next(lbl for lbl in ("APPROVED", "SKIPPED", "SAVED", "SENT") if lbl in ln) for ln in lines]
    assert order == ["APPROVED", "SKIPPED", "SAVED", "SENT", "SAVED"]
    assert "ACME GmbH" in text and "→ jobs@" in text  # narrow terminals shorten the detail, not the kind or company
    if width >= 100:
        assert "→ jobs@acme.de" in text
    assert "llm" not in text.lower().split("feed")[-1]  # hidden kinds are not feed lines


def test_dry_run_wording():
    rs, events = scripted(dry_run=True)
    text = to_text(render_run_view(rs, events, 120, 40, now=T0), 120)
    assert "DRY RUN" in text and "WOULD SEND" in text and "would send 1" in text
    assert "SENT  " not in text and "emails sent" not in text


def test_filter_shows_emails_and_forms_only():
    rs, events = scripted()
    text = to_text(render_run_view(rs, events, 120, 40, only_emails_and_forms=True, now=T0), 120)
    assert "SAVED" in text and "SENT" in text and "APPROVED" not in text and "SKIPPED" not in text


def test_feed_keeps_the_newest_lines_within_its_height():
    rs = RunStats(started_at=T0.isoformat())
    events = [_ev("folder_saved", i, company=f"Firm {i}", data={"channel": "email"}) for i in range(300)]
    text = to_text(render_run_view(rs, events, 120, 30, now=T0), 120)
    assert "Firm 299" in text and "Firm 0," not in text


def test_final_summary_waits_for_a_key():
    rs, events = scripted()
    rs.apply(RunEvent("run_finished", data={"ok": True}))
    text = to_text(render_run_view(rs, events, 120, 40, final=True, now=T0), 120)
    assert "finished without errors" in text and "Press any key" in text


def test_stale_marker():
    rs, events = scripted()
    text = to_text(render_run_view(rs, events, 120, 40, stale=True, now=T0), 120)
    assert "STALE" in text


def test_each_model_shows_its_calls_tokens_and_cost(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path), start_run=False)
    now = datetime.now(UTC).isoformat()
    rows = [("openrouter", "google/gemini-3.8-flash", "evaluate", 2000, 300, 500),
            ("openrouter", "google/gemini-3.8-flash", "evaluate", 1000, 100, 0),
            ("openrouter", "anthropic/claude-sonnet-5.5", "write", 4000, 900, 0),
            ("openrouter", "x/unpriced", "form_find", 50, 5, 0)]
    for provider, model, task, tin, tout, cached in rows:
        ctx.conn.execute("INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, cached_input_tokens, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (provider, model, task, tin, tout, cached, now))
    ctx.conn.commit()
    prices = PriceTable.load({}, fetched=parse_models({"data": [
        {"id": "google/gemini-3.8-flash", "pricing": {"prompt": "0.00000075", "completion": "0.00000375",
                                                      "input_cache_read": "0.000000075"}},
        {"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt": "0.000002", "completion": "0.00001"}}]}))
    st = compute_stats(ctx.conn, ctx.config, prices, "today")
    by_model = {m.model: m for m in st.per_model}
    flash = by_model["google/gemini-3.8-flash"]
    assert (flash.calls, flash.input_tokens, flash.cached_tokens, flash.output_tokens) == (2, 3000, 500, 400)
    assert flash.tasks == {"evaluate": 2} and by_model["anthropic/claude-sonnet-5.5"].tasks == {"write": 1}
    assert abs(flash.cost - (2500 * 0.75 + 500 * 0.075 + 400 * 3.75) / 1e6) < 1e-9
    assert by_model["x/unpriced"].cost is None  # unknown, never zero
    assert st.tokens == (2000 + 1000 + 4000 + 50) + (300 + 100 + 900 + 5)  # cached tokens are part of the input, not extra
    for width in (60, 140):
        text = to_text(render_stats_page(st, width), width)
        assert "AI usage per model" in text and "google/gemini-3.8-flash" in text and "claude-sonnet-5.5" in text
        assert "unknown" in text
    ctx.close()


def test_each_task_line_shows_its_model_calls_and_tokens_in_the_run_view():
    rs = RunStats(run_id="r1", started_at=T0.isoformat(), models={
        "evaluate": "openrouter x/cheap (low)", "write": "openrouter y/pricey (medium)",
        "assist form-fill": "openrouter y/pricey (low)", "find form": "openrouter x/cheap (low)"})
    for n, (task, tin, cached, tout, cost) in enumerate([
            ("evaluate", 1000, 200, 100, 0.0004), ("evaluate", 500, 0, 50, 0.0002),
            ("write", 3000, 0, 700, 0.01), ("form_find", 10, 0, 5, None)]):
        rs.apply(_ev("llm_call", n, data={"tokens": tin + tout, "cost_usd": cost, "task": task, "provider": "openrouter",
                                          "model": "m", "input_tokens": tin, "cached_tokens": cached,
                                          "output_tokens": tout}))
    assert rs.tokens == 1100 + 550 + 3700 + 15 and rs.llm_calls == 4  # the totals stay
    evaluate = rs.task_usage["evaluate"]
    assert (evaluate["calls"], evaluate["input"], evaluate["cached"], evaluate["output"]) == (2, 1500, 200, 150)
    assert evaluate["cost"] == pytest.approx(0.0006) and rs.task_usage["form_find"]["cost"] is None
    assert RunStats.from_dict(rs.to_dict()).task_usage == rs.task_usage  # another terminal sees the same numbers
    for width in (60, 160):
        text = " ".join(to_text(render_run_top(rs, width, now=T0 + timedelta(seconds=5)), width).split())
        assert "evaluate: openrouter x/cheap (low)" in text and "2 calls, 1,650 tokens, $0.0006" in text
        assert "1 call, 3,700 tokens, $0.0100" in text  # write
        assert "1 call, 15 tokens, cost unknown" in text  # find form
        assert "no calls" in text  # assist form-fill is not used during a run
        assert "AI calls" in text and "5,365" in text  # totals


def test_stats_page_task_lines_show_the_usage_of_the_period(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path), start_run=False)
    now = datetime.now(UTC).isoformat()
    for task, tin, tout in (("evaluate", 1000, 100), ("evaluate", 1000, 100), ("write", 2000, 500)):
        ctx.conn.execute("INSERT INTO llm_usage (provider, model, task, input_tokens, output_tokens, created_at) "
                         "VALUES ('anthropic', 'claude-sonnet-5-5', ?, ?, ?, ?)", (task, tin, tout, now))
    ctx.conn.commit()
    st = compute_stats(ctx.conn, ctx.config, PriceTable.load({}), "today",
                       models={"evaluate": "anthropic claude-sonnet-5-5 (low)", "write": "anthropic claude-sonnet-5-5",
                               "find form": "anthropic claude-sonnet-5-5 (low)"})
    text = to_text(render_stats_page(st, 160), 160)
    assert "2 calls, 2,200 tokens" in text and "1 call, 2,500 tokens" in text and "no calls" in text
    assert "calls" in text.split("AI usage (estimated)")[1]  # the totals panel is still there
    ctx.close()


def test_tags_name_the_thing_and_the_outcome_and_share_one_width():
    from autoapp.employers.service import resolved_text
    from autoapp.events import LABEL_WIDTH, LABELS

    assert LABELS["employer_resolved"] == "EMPLOYER SITE" and LABELS["employer_matched"] == "JOB FOUND"
    assert LABELS["employer_unresolved"] == "JOB NOT FOUND" and LABELS["email_sent"] == "EMAIL SENT"
    assert LABELS["email_held"] == "EMAIL HELD" and LABELS["folder_saved"] == "FOLDER SAVED"
    assert len(set(LABELS.values())) == len(LABELS)  # no two events share a tag
    assert LABEL_WIDTH == max(map(len, LABELS.values()))
    lines = [feed_line(_ev(kind, 0, company="ACME GmbH", title="Data Engineer", detail="x"), 120).plain
             for kind in LABELS]
    assert len({line.index("ACME") for line in lines}) == 1  # the company starts in the same column in every line
    assert resolved_text("FEAG GmbH", "verified", "cache") == "FEAG GmbH: website verified (saved site)"
    assert resolved_text("X", "verified", "search").endswith("(new lookup)")
    assert "no website could be verified" in resolved_text("X", "unresolved", None)


@pytest.mark.parametrize(("width", "height"), [(60, 50), (120, 30), (120, 45)])
def test_the_whole_run_view_fits_the_terminal_and_keeps_its_footer(width, height):
    rs, events = scripted()
    events = [_ev("search", i % 50, detail=f"line {i}") for i in range(150)] + events
    text = to_text(render_run_view(rs, events, width, height, now=T0), width)
    lines = text.rstrip("\n").splitlines()
    assert len(lines) <= height, (len(lines), height)
    assert "q: close this view" in "\n".join(lines[-2:])


def test_scrolling_back_shows_older_lines_and_says_so():
    rs, _ = scripted()
    events = [_ev("job_approved", 1, company=f"marker-{i:03d}", title="") for i in range(100)]
    newest = to_text(render_run_view(rs, events, 120, 40, now=T0), 120)
    older = to_text(render_run_view(rs, events, 120, 40, now=T0, scroll=30), 120)
    assert "marker-099" in newest and "marker-099" not in older
    assert "marker-069" in older and "30 newer lines below" in older
