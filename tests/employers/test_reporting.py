"""Events, run summary, statistics, menu and CSV for employer discovery (task 5.5)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.config.models import load_config
from autoapp.dashboard.queries import compute_stats
from autoapp.dashboard.view import render_run_top, render_stats_page, to_text
from autoapp.dashboard.wiring import attach_run_events
from autoapp.employers.service import run_discovery
from autoapp.events import RunEvent, RunStats
from autoapp.llm.cost import PriceTable
from autoapp.menu import main as menu
from autoapp.setup.checks import FakeChecks
from autoapp.setup.prompts import ScriptedPrompter
from autoapp.setup.wizard import use_runtime
from autoapp.steps.context import RunContext
from tests.employers.identity_world import search_client
from tests.employers.test_service import EMP_CONFIG, add_alert, cbc_pages
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace

runner = CliRunner()
SETUP = """
setup:
  version: 1
  completed_sections: [documents, profile, salary, search, sources, llm, email, writing]
"""


@pytest.fixture
def ran(tmp_path):
    ws = make_workspace(tmp_path, config=EMP_CONFIG + SETUP)
    ctx = RunContext.open(ws)
    stats = attach_run_events(ctx, [])
    add_alert(ctx)
    api, _ = search_client(["https://www.cbc-cologne.de/karriere"])
    summary = run_discovery(ctx, fetch=FakeWeb(cbc_pages()), api=api)
    return ws, ctx, stats, summary


def test_run_counters_are_separate_from_job_totals(ran):
    ws, ctx, stats, summary = ran
    assert (stats.employers_seen, stats.employers_cached, stats.employer_scans) == (1, 0, 1)
    assert (stats.employer_alerts_matched, stats.employer_alerts_recovered, stats.employer_jobs_added) == (1, 1, 1)
    assert stats.employer_searches == 1 and stats.employer_search_usd == pytest.approx(0.001)
    assert (stats.ads_found, stats.ads_new, stats.filtered) == (0, 0, 0)  # the existing job counts are untouched
    ctx.close()


def test_run_view_shows_the_employer_step_and_counters(ran):
    ws, ctx, stats, _ = ran
    text = to_text(render_run_top(stats, 120), 120)
    assert "employer sites" in text and "alerts at employer 1 (1 recovered)" in text and "employer jobs 1" in text
    narrow = to_text(render_run_top(stats, 70), 70)
    assert "employer sites: 1 (0 saved)" in narrow
    plain = to_text(render_run_top(RunStats(started_at="2026-10-09T10:00:00+00:00"), 120), 120)
    assert "employer" not in plain  # nothing is shown when discovery did not run
    ctx.close()


def test_statistics_have_an_employer_panel_with_usage_and_limits(ran):
    ws, ctx, stats, _ = ran
    config = load_config(ws)
    st = compute_stats(ctx.conn, config, PriceTable.load(config.llm.prices), "all")
    e = st.employer
    assert (e.verified_sites, e.alerts_matched, e.alerts_recovered, e.jobs_added) == (1, 1, 1, 1)
    assert e.scans == {"complete": 1} and e.searches == 1 and e.estimated_usd == pytest.approx(0.001)
    assert e.search_limits["today"] == (1, 1000)
    text = to_text(render_stats_page(st, 140), 140)
    assert "Employer sites" in text and "1 verified" in text and "not a bill" in text
    run = compute_stats(ctx.conn, config, PriceTable.load(config.llm.prices), "run")
    assert run.employer.searches == 1
    ctx.close()


def test_statistics_hide_the_panel_when_discovery_is_off_and_unused(tmp_path):
    ws = make_workspace(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: false"))
    ctx = RunContext.open(ws)
    config = load_config(ws)
    st = compute_stats(ctx.conn, config, PriceTable.load(config.llm.prices), "all")
    assert st.employer is None and "Employer sites" not in to_text(render_stats_page(st, 140), 140)
    ctx.close()


def test_feed_events_are_quiet_and_labelled():
    from autoapp.dashboard.view import feed_line

    for kind in ("employer_matched", "employer_job_added", "employer_deferred"):
        e = RunEvent(kind, company="CBC", detail="x")
        assert e.level == "quiet" and feed_line(e, 100).plain.split()[1] not in ("", kind.upper())
    assert RunEvent("employer_search_usage").level == "hidden"


def test_summary_lines_describe_each_part_without_inflating_jobs(ran):
    ws, ctx, stats, summary = ran
    lines = "\n".join(summary.lines())
    assert "employer sites: 1 employers, 1 newly verified" in lines
    assert "alert vacancies: 1 matched at the employer (1 descriptions recovered), 0 unresolved, 0 not found; 1 new employer jobs added" in lines
    assert "employer search: 1 requests (0 failed), estimated $0.001 gross" in lines
    ctx.close()


def test_menu_has_an_employer_entry_that_shows_and_previews(ran):
    ws, ctx, *_ = ran
    ctx.close()
    assert "employers" in [e.key for e in menu.ENTRIES]
    answers = [("What would you like", "employers"), ("Employer sites", "show"), ("Employer sites", "preview"),
               ("Employer sites", menu.BACK), ("What would you like", "quit")]
    p = ScriptedPrompter(answers)
    with use_runtime(prompter=p, checks=FakeChecks()):
        r = runner.invoke(app, ["-w", str(ws.root)], catch_exceptions=False)
    assert r.exit_code == 0 and p.remaining == 0
    out = "\n".join(p.output) + r.output
    assert "Employer sites (1 saved)" in out and "Nothing was changed" in out  # the only alert is already matched
