"""The main menu (add-menu-and-run-modes 4.1-4.6)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.lock import workspace_lock
from autoapp.menu import main as menu
from autoapp.setup.checks import CheckResult, FakeChecks
from autoapp.setup.prompts import ScriptedPrompter
from autoapp.setup.wizard import use_runtime
from autoapp.steps.context import RunContext
from tests.helpers import make_workspace
from tests.test_cli_commands import CONFIG, SETUP

runner = CliRunner()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return make_workspace(tmp_path, config=CONFIG + SETUP)


def open_menu(ws, answers, *, input=None, checks=None):
    p = ScriptedPrompter(answers)
    with use_runtime(prompter=p, checks=checks or FakeChecks()):
        r = runner.invoke(app, ["-w", str(ws.root)], input=input, catch_exceptions=False)
    return r, p


# --- 4.1 opening, looping, leaving ---------------------------------------------------------------


def test_without_a_terminal_autoapp_prints_help(ws):
    r = runner.invoke(app, ["-w", str(ws.root)])
    assert r.exit_code == 0 and "Usage" in r.output and "run" in r.output


def test_non_interactive_flag_prints_help_even_with_a_prompter(ws):
    with use_runtime(prompter=ScriptedPrompter([])):
        r = runner.invoke(app, ["-w", str(ws.root), "--non-interactive"])
    assert r.exit_code == 0 and "Usage" in r.output


def test_menu_loops_until_quit(ws, monkeypatch):
    calls = []
    monkeypatch.setattr("autoapp.dashboard.show_stats", lambda ws, period, **kw: calls.append(period))
    r, p = open_menu(ws, [("What would you like", "stats"), ("What would you like", "stats"),
                          ("What would you like", "quit")])
    assert r.exit_code == 0 and calls == ["today", "today"] and p.remaining == 0
    assert p.choice_values[0] == [e.key for e in menu.ENTRIES]


def test_ctrl_c_in_an_entry_returns_to_the_menu(ws, monkeypatch):
    def interrupted(tctx, p):
        raise KeyboardInterrupt

    monkeypatch.setattr(menu, "entry_add", interrupted)
    r, p = open_menu(ws, [("What would you like", "add"), ("What would you like", "quit")])
    assert r.exit_code == 0 and "Cancelled." in p.output and p.remaining == 0


def test_a_failing_entry_does_not_end_the_menu(ws, monkeypatch):
    monkeypatch.setattr(menu, "entry_stats", lambda tctx, p: 1 / 0)
    r, p = open_menu(ws, [("What would you like", "stats"), ("What would you like", "quit")])
    assert r.exit_code == 0 and any("error: division by zero" in line for line in p.output)


def test_no_lock_is_held_while_the_menu_waits(ws):
    class LockProbe(ScriptedPrompter):
        def select(self, message, choices, default=None):
            with workspace_lock(ws):  # a scheduled run could start now
                pass
            return super().select(message, choices, default)

    p = LockProbe([("What would you like", "quit")])
    with use_runtime(prompter=p):
        assert runner.invoke(app, ["-w", str(ws.root)], catch_exceptions=False).exit_code == 0


def test_first_start_runs_the_wizard_before_the_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    ws = make_workspace(tmp_path)  # setup not complete
    order = []
    monkeypatch.setattr("autoapp.setup.gate.require_setup", lambda ws, interactive: order.append("setup"))
    monkeypatch.setattr("autoapp.menu.main.main_menu", lambda ctx: order.append("menu"))
    with use_runtime(prompter=ScriptedPrompter([])):
        runner.invoke(app, ["-w", str(ws.root)], catch_exceptions=False)
    assert order == ["setup", "menu"]


# --- 4.2 entries call the commands' functions ----------------------------------------------------------


def test_run_now_calls_run_with_mode(ws, monkeypatch):
    seen = {}
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda tctx, **kw: seen.update(kw))
    open_menu(ws, [("What would you like", "run"), ("What would you like", "quit")])
    assert seen["mode"] is None and seen["dry_run"] is False and seen["interactive"] is True


def test_add_calls_add_and_offers_to_paste(ws, monkeypatch):
    added = {}

    def fake_add(tctx, url, profile):
        added.update(url=url, profile=profile)
        ctx = RunContext.open(ws, start_run=False)
        jid = ctx.repo.insert_job(title="(title pending)", company="x", status="description_unavailable")
        ctx.repo.add_source(jid, "manual", "https://jobs.example.de/1")
        ctx.close()

    described = {}
    monkeypatch.setattr("autoapp.commands.add", fake_add)
    monkeypatch.setattr("autoapp.commands.describe",
                        lambda tctx, job_id, file: described.update(text=open(file).read()))
    open_menu(ws, [("What would you like", "add"), ("Link", "https://jobs.example.de/1"),
                   ("Paste the ad text now", True), ("Paste the job ad", "Wir suchen ..."),
                   ("What would you like", "quit")])
    assert added == {"url": "https://jobs.example.de/1", "profile": None} and described == {"text": "Wir suchen ..."}


def test_finish_forms_with_nothing_prepared(ws):
    r, p = open_menu(ws, [("What would you like", "forms"), ("What would you like", "quit")])
    assert "No prepared forms." in p.output


def test_badges_count_forms_and_review(ws):
    ctx = RunContext.open(ws, start_run=False)
    a = ctx.repo.insert_job(title="A", company="Form AG", status="prepared")
    ctx.repo.upsert_application(a, channel="employer_form")
    ctx.repo.insert_job(title="B", company="R AG", status="review")
    ctx.close()
    assert menu.badges(ws) == {"forms": "1 prepared", "review": "1 waiting"}


def _form_job(ws, title, form_status=None, note=None):
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title=title, company="Form AG", status="prepared")
    ctx.repo.upsert_application(jid, channel="employer_form", form_status=form_status, form_note=note)
    ctx.close()
    return jid


def test_finish_forms_opens_the_picked_job_without_asking_first(ws, monkeypatch):
    a = _form_job(ws, "A", "found")
    opened = []
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: opened.append(job_id))
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", a),
                          ("Which application", menu.BACK), ("What would you like", "quit")])
    assert opened == [a] and p.remaining == 0


def test_after_each_job_the_list_comes_back_even_after_an_error(ws, monkeypatch):
    a = _form_job(ws, "A", "found")
    opened = []

    def crashing_assist(tctx, job_id):
        opened.append(job_id)
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr("autoapp.commands.assist", crashing_assist)
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", a), ("Which application", a),
                          ("Which application", menu.BACK), ("What would you like", "quit")])
    assert opened == [a, a] and p.remaining == 0
    assert any("browser has been closed" in line for line in p.output)


def test_forms_the_app_can_fill_come_first_with_a_plain_reason(ws):
    from types import SimpleNamespace

    gone = _form_job(ws, "Gone", "unreachable", "HTTP 404")
    found = _form_job(ws, "Found", "found", "application form found")
    click = _form_job(ws, "Click", "partial", "no unambiguous safe navigation; continue manually")
    p = ScriptedPrompter([("Which application", menu.BACK)])
    menu.finish_forms(SimpleNamespace(obj=SimpleNamespace(workspace=ws)), p)
    assert p.choice_values[0] == [found, click, gone, menu.BACK]


def test_after_a_run_enter_means_not_now(ws, monkeypatch):
    from types import SimpleNamespace

    from autoapp.setup.prompts import ENTER

    _form_job(ws, "A", "found")
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: pytest.fail("no browser should open"))
    p = ScriptedPrompter([("Which application", ENTER)])
    assert menu.finish_forms(SimpleNamespace(obj=SimpleNamespace(workspace=ws)), p, after_run=True) == \
        "No application opened."


def test_a_form_the_app_cannot_reach_is_explained_and_never_opened_in_its_browser(ws, monkeypatch):
    gone = _form_job(ws, "Gone", "restricted", "restricted target: www.linkedin.com")
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: pytest.fail("no browser should open"))
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", gone),
                          ("What do you want to do", "skip"), ("What would you like", "quit")])
    assert p.choice_values[2] == ["address", "link", "submitted", "skip", menu.BACK]
    assert any("linkedin.com" in line and "never opens" in line for line in p.output)
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(gone)["status"] == "skipped"
    ctx.close()


def test_opening_the_job_link_stays_in_the_menu_so_the_form_address_can_be_pasted_next(ws, monkeypatch):
    from autoapp.setup.prompts import ENTER

    job = _form_job(ws, "Apply yourself", "restricted", "restricted target: www.linkedin.com")
    ctx = RunContext.open(ws, start_run=False)
    ctx.repo.update_job(job, application_url="https://www.linkedin.com/jobs/view/7/")
    ctx.close()
    opened, assisted = [], []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: assisted.append(job_id))
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", job),
                          ("What do you want to do", "link"),
                          ("What do you want to do", ENTER), ("Address of the application form", ""),
                          ("What do you want to do", "address"),
                          ("Address of the application form", "https://stellenanzeigen.example.com/Vacancies/1864"),
                          ("Which application", menu.BACK), ("What would you like", "quit")])
    assert len(opened) == 1 and assisted == [job] and p.remaining == 0
    # After opening the link, Enter means "paste its address" (an empty address keeps you in the same menu).
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(job)["application_url"] == "https://stellenanzeigen.example.com/Vacancies/1864"
    ctx.close()


def test_arbeitsagentur_link_can_be_opened_manually_in_normal_browser(ws, monkeypatch):
    job = _form_job(ws, "Agentur", "partial", "only on the Arbeitsagentur: posted directly there")
    aa_url = "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-1234567890-S"
    ctx = RunContext.open(ws, start_run=False)
    ctx.repo.update_job(job, application_url=aa_url)
    ctx.close()

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    r, p = open_menu(ws, [
        ("What would you like", "forms"), ("Which application", job),
        ("What do you want to do", "link"), ("What do you want to do", menu.BACK),
        ("Which application", menu.BACK), ("What would you like", "quit"),
    ])

    assert r.exit_code == 0 and opened == [aa_url] and p.remaining == 0
    assert any(f"Opened {aa_url}" in line for line in p.output)


def test_normal_browser_launch_failure_reports_the_url(ws, monkeypatch):
    job = _form_job(ws, "Manual", "partial", "continue manually")
    ctx = RunContext.open(ws, start_run=False)
    ctx.repo.update_job(job, application_url="https://employer.example/apply")
    ctx.close()
    monkeypatch.setattr("webbrowser.open", lambda url: False)
    result, prompts = open_menu(ws, [
        ("What would you like", "forms"), ("Which application", job),
        ("What do you want to do", "link"), ("What do you want to do", menu.BACK),
        ("Which application", menu.BACK), ("What would you like", "quit"),
    ])
    assert result.exit_code == 0
    assert any("Could not launch" in line and "https://employer.example/apply" in line for line in prompts.output)
    assert not any("Opened https://employer.example/apply" in line for line in prompts.output)


def test_a_page_without_a_clear_apply_button_says_what_to_click(ws, monkeypatch):
    job = _form_job(ws, "Agentur", "partial", "no unambiguous safe navigation; continue manually")
    opened = []
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: opened.append(job_id))
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", job),
                          ("What do you want to do", "browser"), ("Which application", menu.BACK),
                          ("What would you like", "quit")])
    assert opened == [job]
    text = "\n".join(p.output)
    assert "unambiguous" not in text and "click the apply button yourself" in text


# --- 4.3 applications ---------------------------------------------------------------------------------------


def _prepared_email_job(ws):
    from tests.steps.test_modes import prepared

    ctx = RunContext.open(ws, start_run=False)
    (jid,) = prepared(ctx)
    ctx.close()
    return jid


def test_save_as_draft_from_applications(ws, monkeypatch):
    from tests.steps.test_modes import FakeDraftStore

    jid = _prepared_email_job(ws)
    store = FakeDraftStore()
    monkeypatch.setattr("autoapp.steps.drafts.default_store", lambda ctx: store)
    r, p = open_menu(ws, [("What would you like", "apps"), ("Which applications", ""), ("Which application", jid),
                          ("What do you want to do", "draft"), ("What do you want to do", menu.BACK),
                          ("What would you like", "quit")])
    assert len(store.drafts) == 1 and "Entwürfe" in r.output
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(jid)["status"] == "drafted"
    ctx.close()


def test_change_status_records_history(ws):
    jid = _prepared_email_job(ws)
    open_menu(ws, [("What would you like", "apps"), ("Which applications", "prepared"), ("Which application", jid),
                   ("What do you want to do", "status"), ("New status", "interview"),
                   ("What do you want to do", menu.BACK), ("What would you like", "quit")])
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(jid)["status"] == "interview" and ctx.repo.history(jid)[-1]["to_status"] == "interview"
    ctx.close()


def test_send_now_needs_confirmation(ws, monkeypatch):
    from autoapp.mail.connection import FakeMailer

    jid = _prepared_email_job(ws)
    mailer = FakeMailer()
    monkeypatch.setattr("autoapp.steps.send.default_mailer", lambda ctx: mailer)
    r, _ = open_menu(ws, [("What would you like", "apps"), ("Which applications", ""), ("Which application", jid),
                          ("What do you want to do", "send"), ("What do you want to do", menu.BACK),
                          ("What would you like", "quit")], input="n\n")
    assert "Send this application now?" in r.output and mailer.sent == []


# --- 4.4 review queue ---------------------------------------------------------------------------------------


def test_review_apply_approves(ws):
    ctx = RunContext.open(ws, start_run=False)
    jid = ctx.repo.insert_job(title="Dev", company="Maybe GmbH", status="review")
    ctx.repo.add_evaluation(jid, fit_score=70, decision="maybe", payload={"result": {"rationale": "close match"}})
    ctx.close()
    r, p = open_menu(ws, [("What would you like", "review"), ("Which job", jid), ("Decision", "apply"),
                          ("What would you like", "quit")])
    assert any("close match" in line for line in p.output)
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(jid)["status"] == "approved"
    ctx.close()


# --- 4.5 tests --------------------------------------------------------------------------------------------------


def test_check_logins_reports_every_check(ws):
    ws.config_path.write_text(ws.config_path.read_text())
    checks = FakeChecks(smtp=CheckResult(False, "SMTP login failed: bad password"))

    class Store:
        def drafts_folder(self):
            return "[Gmail]/Entw&APw-rfe"

        def close(self):
            pass

    lines = menu.check_logins(ws, checks=checks, draft_store_factory=Store, adzuna_check=lambda: (True, ""))
    text = "\n".join(lines)
    assert "FAIL  sending mailbox SMTP" in text and "bad password" in text and "Settings > email" in text
    assert "PASS  sending mailbox IMAP, Drafts folder found: [Gmail]/Entwürfe" in text
    assert "PASS  AI model" in text


def test_check_logins_asks_search_first_for_its_search_key(tmp_path, monkeypatch):
    from tests.helpers import make_workspace

    ws = make_workspace(tmp_path, alert_mode="search_first")
    checks = FakeChecks()
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    text = "\n".join(menu.check_logins(ws, checks=checks, draft_store_factory=lambda: type(
        "S", (), {"drafts_folder": lambda self: "Drafts", "close": lambda self: None})(), adzuna_check=lambda: (True, "")))
    assert "FAIL  search key for search-first alert jobs" in text
    monkeypatch.setenv("PARALLEL_API_KEY", "pk-x")
    text = "\n".join(menu.check_logins(ws, checks=checks, draft_store_factory=lambda: type(
        "S", (), {"drafts_folder": lambda self: "Drafts", "close": lambda self: None})(), adzuna_check=lambda: (True, "")))
    assert "PASS  search key for search-first alert jobs" in text


def test_quick_test_is_the_first_and_default_action_of_the_tests_entry(ws, monkeypatch):
    calls = []
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda tctx, **kw: calls.append(kw))
    r, p = open_menu(ws, [("What would you like", "tests"), ("Which test", None), ("What would you like", "quit")])
    choices = next(v for v in p.choice_values if "quick" in v)
    assert choices[0] == "quick"
    assert [(c["mode"], c["fresh"], c["sample"], c["ask_mode"], c["dry_run"]) for c in calls] == [
        ("test", True, True, False, False)]  # Enter picks the quick test: no mode question, a fresh sampled test run
    assert any("Quick test finished." in line for line in p.output)


def test_test_run_and_dry_run_skip_the_mode_question(ws, monkeypatch):
    calls = []
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda tctx, **kw: calls.append(kw))
    open_menu(ws, [("What would you like", "tests"), ("Which test", "testrun"),
                   ("What would you like", "tests"), ("Which test", "dry"), ("What would you like", "quit")])
    assert [(c["mode"], c["dry_run"], c["ask_mode"]) for c in calls] == [("test", False, False), (None, True, False)]


def test_test_email_for_one_application(ws, monkeypatch):
    jid = _prepared_email_job(ws)
    sent = []
    monkeypatch.setattr("autoapp.commands.send", lambda tctx, **kw: sent.append(kw))
    open_menu(ws, [("What would you like", "tests"), ("Which test", "email"), ("Which application", jid),
                   ("What would you like", "quit")])
    assert sent == [{"job_id": jid, "yes": False, "test": True}]


# --- 4.6 settings ------------------------------------------------------------------------------------------------


def test_default_mode_round_trip_keeps_comments(ws):
    ws.config_path.write_text("# my comment\n" + ws.config_path.read_text() + "email:\n  auto_send: true\n  daily_cap: 3\n")
    open_menu(ws, [("What would you like", "settings"), ("Settings", "mode"), ("Default run mode", "review"),
                   ("What would you like", "quit")])
    text = ws.config_path.read_text()
    assert "# my comment" in text and "default_mode: review" in text and "auto_send" not in text
    assert "daily_cap: 3" in text
    from autoapp.config.models import load_config

    assert load_config(ws).run.default_mode == "review"


def test_edit_one_section_runs_that_section(ws, monkeypatch):
    ran = []
    monkeypatch.setattr("autoapp.setup.wizard.run_section", lambda ctx, name: ran.append(name) or True)
    r, p = open_menu(ws, [("What would you like", "settings"), ("Settings", "edit:search"),
                          ("What would you like", "quit")])
    assert ran == ["search"] and "Saved the search section." in p.output


def test_redo_everything_backs_up_first(ws, monkeypatch):
    seen = {}

    def fake_onboarding(ctx, only_missing=True):
        seen["backups"] = list(ws.backups_dir.iterdir())
        seen["only_missing"] = only_missing
        return True

    monkeypatch.setattr("autoapp.setup.wizard.run_onboarding", fake_onboarding)
    open_menu(ws, [("What would you like", "settings"), ("Settings", "redo"), ("Redo every section", True),
                   ("What would you like", "quit")])
    assert seen["only_missing"] is False and len(seen["backups"]) == 1
    assert (seen["backups"][0] / "config.yaml").exists()


def test_section_summaries(ws):
    s = menu.section_summaries(ws)
    assert "keywords" in s["search"] and "default mode review" in s["email"]
    assert "alert emails (platform)" in s["sources"]  # the test workspaces keep the earlier alert mode


def test_section_summaries_name_the_default_search_first_mode(tmp_path):
    from tests.helpers import make_workspace

    s = menu.section_summaries(make_workspace(tmp_path, alert_mode="search_first"))
    assert "alert emails (search first)" in s["sources"]


def test_a_pasted_form_address_becomes_the_application_link_and_opens(ws, monkeypatch):
    job = _form_job(ws, "Easy", "restricted", "restricted target: www.linkedin.com")
    opened = []
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: opened.append(job_id))
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", job),
                          ("What do you want to do", "address"),
                          ("Address of the application form", "https://www.linkedin.com/jobs/view/1"),  # refused
                          ("What do you want to do", "address"),  # still the same job's menu
                          ("Address of the application form", "https://firma.jobs.personio.de/job/1/apply"),
                          ("Which application", menu.BACK), ("What would you like", "quit")])
    assert opened == [job] and p.remaining == 0
    ctx = RunContext.open(ws, start_run=False)
    assert ctx.repo.get_job(job)["application_url"] == "https://firma.jobs.personio.de/job/1/apply"
    assert ctx.repo.get_application(job)["form_status"] == "partial"
    ctx.close()


def test_a_linkedin_job_offers_reading_the_company_link_only_when_switched_on(ws, monkeypatch):
    from autoapp.setup.wizard import write_key

    job = _form_job(ws, "Linked", "restricted", "restricted target: www.linkedin.com")
    ctx = RunContext.open(ws, start_run=False)
    ctx.repo.update_job(job, application_url="https://www.linkedin.com/jobs/view/7/")
    ctx.close()
    opened = []
    monkeypatch.setattr("autoapp.commands.assist", lambda tctx, job_id: opened.append(job_id))
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", job),
                          ("What do you want to do", menu.BACK), ("Which application", menu.BACK),
                          ("What would you like", "quit")])
    assert "browser" not in p.choice_values[2] and opened == []  # the actions for this job
    write_key(ws.config_path, "sources", {"apply_links_with_login": {"linkedin": True}})
    r, p = open_menu(ws, [("What would you like", "forms"), ("Which application", job),
                          ("What do you want to do", "browser"), ("Which application", menu.BACK),
                          ("What would you like", "quit")])
    assert "browser" in p.choice_values[2] and opened == [job]


def test_the_full_test_run_entry_is_a_fresh_unsampled_test_run(ws, monkeypatch):
    calls = []
    monkeypatch.setattr("autoapp.commands.run_with_mode", lambda tctx, **kw: calls.append(kw))
    r, p = open_menu(ws, [("What would you like", "tests"), ("Which test", "fresh"), ("What would you like", "quit")])
    assert [(c["mode"], c["fresh"], c["sample"] if "sample" in c else False, c["ask_mode"]) for c in calls] == [
        ("test", True, False, False)]
    assert any("Full test run finished." in line for line in p.output)
