"""CLI-level tests for the pipeline commands (steps are tested in tests/steps)."""

import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.lock import workspace_lock
from autoapp.steps.context import RunContext
from tests.helpers import CONFIG, make_workspace

SETUP = """
setup:
  version: 1
  completed_sections: [documents, profile, salary, search, sources, llm, email, writing]
"""

runner = CliRunner()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return make_workspace(tmp_path, config=CONFIG + SETUP)


def cli(ws, *args, input=None):
    return runner.invoke(app, ["-w", str(ws.root), *args], input=input, catch_exceptions=False)


def test_add_and_describe(ws, monkeypatch):
    monkeypatch.setattr("autoapp.steps.fetch.fetch_details", lambda ctx, **kw: _unavailable(ctx))
    r = cli(ws, "add", "https://jobs.example-firma.de/stellen/42?utm_source=x")
    assert "Added job" in r.output and "autoapp describe" in r.output
    job_id = r.output.split("Added job ", 1)[1].split(":", 1)[0]
    r = cli(ws, "add", "https://jobs.example-firma.de/stellen/42")
    assert "Already known" in r.output  # tracking params stripped, same job
    r = cli(ws, "describe", job_id, input="Wir suchen eine Python-Entwicklerin.\n")
    assert "will be evaluated next" in r.output
    ctx = RunContext.open(ws, start_run=False)
    job = ctx.repo.get_job(job_id)
    assert job["status"] == "new" and "Python" in job["description"]
    ctx.close()


def _unavailable(ctx):
    from autoapp.db.status import Status
    from autoapp.steps.fetch import FetchSummary

    for j in ctx.repo.jobs_by_status(Status.NEW):
        ctx.repo.transition(j["id"], Status.DESCRIPTION_UNAVAILABLE, note="test")
    return FetchSummary(unavailable=1)


def test_run_dry_run_prints_summary_and_exports(ws, monkeypatch):
    from autoapp.steps.pipeline import RunSummary

    seen = {}

    def fake(ctx, profile=None, **kw):
        seen["dry"] = ctx.dry_run
        return RunSummary()

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", fake)
    r = cli(ws, "run", "--dry-run", "--non-interactive")
    assert r.exit_code == 0 and seen == {"dry": True}
    assert ws.csv_path.exists()


def test_run_exits_nonzero_on_errors(ws, monkeypatch):
    from autoapp.steps.pipeline import RunSummary

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline",
                        lambda ctx, profile=None, **kw: RunSummary(errors=["send: bad password"]))
    r = cli(ws, "run", "--non-interactive")
    assert r.exit_code == 1 and "error: send: bad password" in r.output


def test_concurrent_run_exits_immediately(ws):
    with workspace_lock(ws):
        r = runner.invoke(app, ["-w", str(ws.root), "run", "--non-interactive"])
    assert r.exit_code == 3 and "Another autoapp run is active" in r.output


def test_evaluate_and_prepare_without_jobs(ws):
    assert "0 evaluated" in cli(ws, "evaluate").output
    assert cli(ws, "prepare").exit_code == 0


def test_send_unknown_job_reports(ws):
    r = cli(ws, "send", "nope", "--yes")
    assert "not sent" in r.output


def test_render_refuses_a_folderless_email_application(ws):
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH")
    ctx.repo.upsert_application(jid, channel="email", contact_email="jobs@a-gmbh.de",
                                letter_md="# Bewerbung\n\nSehr geehrte Damen und Herren,\n\nText.\n\n"
                                          "Mit freundlichen Grüßen\nMax Mustermann")
    ctx.close()
    r = cli(ws, "render", jid)
    assert r.exit_code == 1 and f"draft {jid} --again" in r.output


def test_drafts_listing_shows_the_check_note_for_flagged_jobs(ws):
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH")
    ctx.repo.upsert_application(jid, channel="email", contact_email="jobs@a-gmbh.de")
    ctx.repo.set_needs_review(jid, ["score 61 is below min_score 75"])
    ctx.conn.execute(
        "INSERT INTO drafts (job_id, message_id, folder, recipient, subject, state, created_at) "
        "VALUES (?, '<draft@example.com>', 'Entw&APw-rfe', 'jobs@a-gmbh.de', 'Bewerbung: Dev', 'saved', "
        "'2026-01-02T10:00:00+00:00')", (jid,))
    ctx.conn.commit()
    ctx.close()
    r = cli(ws, "drafts")
    assert r.exit_code == 0
    assert f"{jid}" in r.output and "jobs@a-gmbh.de" in r.output
    assert "    check before sending: score 61 is below min_score 75" in r.output


def test_draft_command_saves_from_the_stored_letter_and_prints_the_check_note(ws, monkeypatch):
    from autoapp.llm.fake import FakeLLM
    from autoapp.steps import drafts as drafts_mod
    from autoapp.steps.prepare import prepare_jobs
    from tests.steps.test_modes import FakeDraftStore
    from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

    store = FakeDraftStore()
    monkeypatch.setattr(drafts_mod, "default_store", lambda ctx: store)
    # ``renderer_factory=PdfRenderer`` is bound when draft_applications is defined, so patch the default itself.
    monkeypatch.setitem(drafts_mod.draft_applications.__kwdefaults__, "renderer_factory", factory(FakeRenderer([1])))
    ctx = RunContext.open(ws, start_run=False)
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert ctx.repo.get_application(jid)["folder"] is None
    ctx.repo.set_needs_review(jid, ["score 61 is below min_score 75"])
    ctx.close()
    r = cli(ws, "draft", jid)
    assert r.exit_code == 0, r.output
    assert "Saved the draft in your mailbox's [Gmail]/Entwürfe folder." in r.output
    assert "check before sending: score 61 is below min_score 75" in r.output
    assert len(store.drafts) == 1 and str(store.drafts[0]["To"]) == "jobs@muster-gmbh.de"


def test_prepare_command_reports_stored_letters_in_review_mode(ws, monkeypatch):
    from autoapp.llm.fake import FakeLLM
    from autoapp.steps import prepare as prepare_mod
    from autoapp.steps.context import RunContext
    from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

    ctx = RunContext.open(ws, start_run=False)
    jid = approved_job(ctx)
    ctx.close()
    llm = FakeLLM(script={"write": [LETTER]})
    monkeypatch.setattr("autoapp.steps.context.RunContext.llm", lambda self, task: llm)
    monkeypatch.setitem(prepare_mod.prepare_jobs.__kwdefaults__, "renderer_factory", factory(FakeRenderer([1])))
    r = cli(ws, "prepare")
    assert r.exit_code == 0, r.output
    assert f"{jid}: letter stored; it is drafted in your mailbox by a review run or `autoapp draft {jid}`" in r.output
    assert list(ws.applications_dir.iterdir()) == []


def test_assist_refuses_unprepared(ws):
    assert "unknown job" in cli(ws, "assist", "nope").output
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH")
    ctx.close()
    r = cli(ws, "assist", jid)
    assert r.exit_code == 1 and "autoapp prepare" in r.output


def test_mark_refreshes_csv_without_explicit_export(ws):
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH")
    ctx.repo.transition(jid, "review")
    ctx.close()
    cli(ws, "mark", jid, "rejected", "--note", "Absage per Mail")
    text = ws.csv_path.read_text(encoding="utf-8-sig")
    assert "rejected" in text and "Absage per Mail" in text


def test_invalid_config_exits_2(ws):
    ws.config_path.write_text(ws.config_path.read_text().replace("keywords: [Backend Developer]", "keywords: []"))
    r = runner.invoke(app, ["-w", str(ws.root), "evaluate"])
    assert r.exit_code == 2


def test_review_queue_promote_with_apply(ws):
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH")
    ctx.repo.transition(jid, "review")
    ctx.close()
    assert jid in cli(ws, "list", "--status", "review").output
    cli(ws, "mark", jid, "apply")
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(jid)["status"] == "approved"  # picked up by the next prepare
    ctx.close()


def test_missing_api_key_stops_at_startup(ws, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = runner.invoke(app, ["-w", str(ws.root), "evaluate"])
    assert r.exit_code == 2 and "ANTHROPIC_API_KEY is not set" in r.output


def test_status_reports_unmapped_documents(ws):
    (ws.documents_dir / "Portfolio.pdf").write_bytes(b"%PDF-1.4")
    r = cli(ws, "status")
    assert "not declared in profile.yaml" in r.output and "Portfolio.pdf" in r.output


def test_send_without_job_id_needs_test_flag(ws):
    r = cli(ws, "send")
    assert r.exit_code == 2 and "--test" in r.output


def test_send_test_reports_to_whom_it_went(ws, monkeypatch):
    from autoapp.mail.connection import FakeMailer

    mailer = FakeMailer()
    monkeypatch.setattr("autoapp.steps.send.default_mailer", lambda ctx: mailer)
    r = cli(ws, "send", "--test")
    assert r.exit_code == 0 and "test mail to max.mustermann@example.com" in r.output
    assert [m["To"] for m in mailer.sent] == ["max.mustermann@example.com"]


def test_send_test_error_exits_nonzero(ws):
    r = cli(ws, "send", "nope", "--test")
    assert r.exit_code == 1 and "unknown job nope" in r.output


def test_stats_command(ws):
    r = cli(ws, "stats", "--period", "7d")
    assert r.exit_code == 0 and "Statistics: last 7 days" in r.output and "Models in use" in r.output
    bad = runner.invoke(app, ["-w", str(ws.root), "stats", "--period", "week"])
    assert bad.exit_code == 2 and "valid periods" in bad.output


def test_run_without_terminal_prints_plain_event_lines(ws, monkeypatch):
    import re

    from autoapp.steps.pipeline import RunSummary

    def fake(ctx, profile=None, **kw):
        ctx.emit("folder_saved", {"id": "j1", "company": "ACME", "title": "Dev"}, detail="application_acme")
        return RunSummary()

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", fake)
    r = cli(ws, "run", "--non-interactive")
    assert r.exit_code == 0 and "SAVED" in r.output and "ACME" in r.output
    assert not re.search(r"\x1b\[", r.output)


# --- run modes (add-menu-and-run-modes 1.2) ---------------------------------------------------


def _capture_mode(monkeypatch):
    from autoapp.steps.pipeline import RunSummary

    seen = {}

    def fake(ctx, profile=None, **kw):
        seen["mode"] = ctx.mode
        return RunSummary(mode=ctx.mode)

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", fake)
    return seen


def test_invalid_mode_exits_2(ws):
    r = runner.invoke(app, ["-w", str(ws.root), "run", "--mode", "auto"])
    assert r.exit_code == 2 and "automatic, review and test" in r.output


def test_explicit_mode_skips_the_question(ws, monkeypatch):
    seen = _capture_mode(monkeypatch)
    monkeypatch.setattr("autoapp.commands._interactive", lambda non_interactive: True)
    monkeypatch.setattr("autoapp.menu.run_prompt.choose_run",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("asked")))
    r = cli(ws, "run", "--mode", "test")
    assert r.exit_code == 0 and seen == {"mode": "test"} and "mode: test run" in r.output


def test_unattended_run_uses_the_default_mode(ws, monkeypatch):
    seen = _capture_mode(monkeypatch)
    assert cli(ws, "run", "--non-interactive").exit_code == 0 and seen == {"mode": "review"}
    ws.config_path.write_text(ws.config_path.read_text() + "run:\n  default_mode: automatic\n")
    cli(ws, "run", "--non-interactive")
    assert seen == {"mode": "automatic"}


def test_interactive_run_asks_for_the_mode(ws, monkeypatch):
    from autoapp.menu.run_prompt import RunChoice

    seen = _capture_mode(monkeypatch)
    monkeypatch.setattr("autoapp.commands._interactive", lambda non_interactive: True)
    monkeypatch.setattr("autoapp.menu.run_prompt.choose_run", lambda *a, **kw: RunChoice(mode="automatic"))
    monkeypatch.setattr("autoapp.dashboard.live.can_use_live", lambda console: False)
    assert cli(ws, "run").exit_code == 0 and seen == {"mode": "automatic"}


def test_old_auto_send_prints_a_notice_once(ws, monkeypatch):
    _capture_mode(monkeypatch)
    ws.config_path.write_text(ws.config_path.read_text() + "email:\n  auto_send: true\n")
    r = cli(ws, "run", "--non-interactive")
    assert r.output.count("email.auto_send is replaced by run.default_mode; using automatic") == 1


def test_draft_command_and_list(ws, monkeypatch):
    from tests.steps.test_modes import FakeDraftStore, prepared

    ctx = RunContext.open(ws, start_run=False)
    (jid,) = prepared(ctx)
    ctx.close()
    store = FakeDraftStore()
    monkeypatch.setattr("autoapp.steps.drafts.default_store", lambda ctx: store)
    r = cli(ws, "draft", jid)
    assert r.exit_code == 0 and "Entwürfe" in r.output and "drafted" in r.output and len(store.drafts) == 1
    r = cli(ws, "draft", jid)
    assert "already saved" in r.output and len(store.drafts) == 1
    assert jid in cli(ws, "drafts").output
    r = cli(ws, "drafts", "--check")
    assert "0 sent, 0 missing, 1 still waiting" in r.output


def test_send_on_a_drafted_job_warns(ws, monkeypatch):
    from tests.steps.test_modes import FakeDraftStore, prepared

    ctx = RunContext.open(ws, start_run=False)
    (jid,) = prepared(ctx)
    ctx.close()
    monkeypatch.setattr("autoapp.steps.drafts.default_store", lambda ctx: FakeDraftStore())
    cli(ws, "draft", jid)
    r = cli(ws, "send", jid, input="n\n")
    assert "a draft of this application is saved in your mailbox" in r.output and "not confirmed" in r.output


def test_login_opens_the_sites_login_page_in_the_apps_browser(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from autoapp.cli import app
    from tests.helpers import make_workspace

    ws = make_workspace(tmp_path)
    opened, closed = [], []
    monkeypatch.setattr("autoapp.commands.open_login_page", lambda profile, url: opened.append((profile, url)))
    monkeypatch.setattr("autoapp.commands.close_login_page", lambda: closed.append(True))
    r = CliRunner().invoke(app, ["-w", str(ws.root), "login", "LinkedIn"], input="\n")
    assert r.exit_code == 0, r.output
    assert opened == [(ws.browser_profile_dir, "https://www.linkedin.com/login")] and closed == [True]
    assert "login is kept" in r.output
    bad = CliRunner().invoke(app, ["-w", str(ws.root), "login", "xing"])
    assert bad.exit_code == 2 and "unknown site" in bad.output


@pytest.mark.parametrize("mode, expected", [
    ("search_first", ["search", "alert_ads", "discovery", "fetch"]),
    ("platform", ["search", "fetch"]),
])
def test_search_command_orders_the_alert_steps_by_mode(tmp_path, monkeypatch, mode, expected):
    from autoapp.employers.service import AlertResolutionSummary
    from autoapp.steps.fetch import FetchSummary
    from autoapp.steps.search import SearchSummary

    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    ws = make_workspace(tmp_path, config=CONFIG + SETUP, alert_mode=mode)
    order: list[str] = []

    def step(name, result):
        def fn(*a, **k):
            order.append(name)
            return result
        return fn

    from autoapp.employers.service import DiscoverySummary

    monkeypatch.setattr("autoapp.steps.search.run_search", step("search", SearchSummary()))
    monkeypatch.setattr("autoapp.employers.service.find_alert_ads", step("alert_ads", AlertResolutionSummary(mode=mode)))
    monkeypatch.setattr("autoapp.employers.service.run_discovery", step("discovery", DiscoverySummary()))
    monkeypatch.setattr("autoapp.steps.fetch.fetch_details", step("fetch", FetchSummary()))
    cli(ws, "search")
    assert order == expected


@pytest.mark.parametrize("mode", ["search_first", "platform"])
@pytest.mark.parametrize("careers, arbeitsagentur", [(False, False), (False, True), (True, False), (True, True)])
def test_search_and_run_run_the_same_employer_passes(tmp_path, monkeypatch, mode, careers, arbeitsagentur):
    from autoapp.employers.service import AlertResolutionSummary, DiscoverySummary, OwnAdsSummary
    from autoapp.steps.fetch import FetchSummary
    from autoapp.steps.pipeline import run_pipeline
    from autoapp.steps.search import SearchSummary

    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    config = (CONFIG + SETUP + f"sources:\n  arbeitsagentur:\n    enabled: {str(arbeitsagentur).lower()}\n"
              f"  employer_careers:\n    enabled: {str(careers).lower()}\n")
    ws = make_workspace(tmp_path, config=config, alert_mode=mode)

    def passes(*modules):
        seen: list[str] = []

        def step(name, result):
            def fn(*a, **k):
                seen.append(name)
                return result
            return fn

        steps = {"find_alert_ads": AlertResolutionSummary(mode=mode), "run_discovery": DiscoverySummary(),
                 "find_own_ads": OwnAdsSummary(missed=["x"]), "seed_arbeitsagentur_misses": None}
        for module in modules:
            for name, result in steps.items():
                monkeypatch.setattr(f"{module}.{name}", step(name, result))
        monkeypatch.setattr("autoapp.steps.fetch.fetch_details", lambda ctx, **kw: FetchSummary())
        monkeypatch.setattr("autoapp.steps.pipeline.fetch_details", lambda ctx, **kw: FetchSummary())
        monkeypatch.setattr("autoapp.steps.search.run_search", lambda ctx, *a, **kw: SearchSummary())
        monkeypatch.setattr("autoapp.steps.pipeline.run_search", lambda ctx, *a, **kw: SearchSummary())
        return seen

    seen_search = passes("autoapp.employers.service")
    cli(ws, "search")
    search_steps = list(seen_search)
    seen_run = passes("autoapp.steps.pipeline")
    ctx = RunContext.open(ws)
    run_pipeline(ctx)
    ctx.close()
    assert seen_run == search_steps
    assert ("find_own_ads" in search_steps) == (careers or arbeitsagentur)
