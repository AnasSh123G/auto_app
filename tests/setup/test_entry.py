"""`autoapp setup` entry points: menu, --section, --reset, resume and backups."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autoapp.config.models import load_config, load_profile
from autoapp.setup.checks import FakeChecks
from autoapp.setup.prompts import ENTER, ScriptedPrompter, SetupAborted
from autoapp.setup.wizard import (
    SECTION_ORDER,
    completed_sections,
    is_setup_complete,
    run_setup_command,
    use_runtime,
)
from tests.setup.conftest import (
    ANTHROPIC_KEY,
    documents_answers,
    full_answers,
    profile_answers,
)

FILES = ("config.yaml", "profile.yaml", ".env", "writing_instructions.md")


def snapshot(ws) -> dict[str, bytes]:
    return {f: (ws.root / f).read_bytes() for f in FILES if (ws.root / f).exists()}


def run_cmd(ws, answers, *, section=None, reset=None, checks=None):
    prompter = ScriptedPrompter(answers)
    with use_runtime(prompter, checks or FakeChecks()):
        code = run_setup_command(ws, section=section, reset=reset)
    return code, prompter


def block(text: str, key: str) -> str:
    """The top-level ``key`` block of a YAML text."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{key}:"))
    end = next((i for i in range(start + 1, len(lines)) if lines[i][0] not in " \t-#\n"), len(lines))
    return "".join(lines[start:end])


def add_hand_profile(ws) -> None:
    text = ws.config_path.read_text()
    marker = "search_profiles:\n"
    hand = marker + "  # added by hand\n  - name: rust-dev\n    keywords:\n      - Rust  # my favourite\n"
    ws.config_path.write_text(text.replace(marker, hand, 1))


# ------------------------------------------------------------------------ rerun


def test_section_rerun_changes_only_that_section(finished_ws):
    ws = finished_ws
    add_hand_profile(ws)
    before_cfg, before_profile = ws.config_path.read_text(), ws.profile_path.read_bytes()
    answers = [
        ("Provider for evaluate", "openrouter"), ("Model for evaluate", ENTER), ("Provider for write", "openrouter"),
        ("Model for write", ENTER), ("Provider for form_fill", "openrouter"), ("Model for form_fill", ENTER),
        ("Provider for form_find", "openrouter"), ("Model for form_find", ENTER), ("OpenRouter API key", "sk-or-NEW-SECRET"), ("Test the API key", False),
    ]
    code, prompter = run_cmd(ws, answers, section="llm")
    assert code == 0 and prompter.remaining == 0
    after_cfg = ws.config_path.read_text()
    assert block(after_cfg, "llm") != block(before_cfg, "llm")
    for key in ("search_profiles", "sources", "detail_fetch", "mail", "run", "writing", "setup"):
        assert block(after_cfg, key) == block(before_cfg, key), key
    assert "# added by hand" in after_cfg and "# my favourite" in after_cfg
    assert ws.profile_path.read_bytes() == before_profile
    cfg = load_config(ws)
    assert [p.name for p in cfg.search_profiles] == ["rust-dev", "machine-learning-engineer"]
    assert {cfg.llm.resolve(t)[0] for t in ("evaluate", "write", "form_fill", "form_find")} == {"openrouter"}
    env = ws.env_path.read_text()
    assert "OPENROUTER_API_KEY='sk-or-NEW-SECRET'" in env and ANTHROPIC_KEY in env
    assert "sk-or-NEW-SECRET" not in after_cfg


def test_rerun_shows_current_values_as_defaults(finished_ws):
    ws = finished_ws
    answers = [("First name", ENTER), ("Last name", ENTER), ("Email address", ENTER), ("Phone", ENTER),
               ("Street", ENTER), ("Postal code", ENTER), ("City", ENTER), ("Country", ENTER), ("LinkedIn", ENTER),
               ("Xing", ENTER), ("Work authorization", ENTER), ("Earliest start", ENTER), ("Notice period", ENTER),
               ("German level", ENTER), ("English level", ENTER), ("Add another language", ENTER),
               ("Keep these extra skills", ENTER), ("Add an extra skill", ENTER)]
    before = ws.profile_path.read_text()
    code, _ = run_cmd(ws, answers, section="profile")
    assert code == 0
    assert ws.profile_path.read_text() == before  # Enter everywhere changes nothing
    assert load_profile(ws).personal.first_name == "Max"


def test_unknown_section_and_conflicting_options(ws, capsys):
    assert run_cmd(ws, [], section="nope")[0] == 2
    assert run_cmd(ws, [], reset="nope")[0] == 2
    assert run_cmd(ws, [], section="llm", reset="llm")[0] == 2
    assert "unknown section 'nope'" in capsys.readouterr().err


def test_setup_needs_a_terminal_without_injected_prompts(ws, monkeypatch, capsys):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    assert run_setup_command(ws) == 2
    assert "interactive terminal" in capsys.readouterr().err


# -------------------------------------------------------------------------- menu


def test_menu_offers_four_options_and_changes_nothing_until_chosen(finished_ws):
    ws = finished_ws
    before = snapshot(ws)
    code, p = run_cmd(ws, [("What would you like", "cancel")])
    assert code == 0
    assert p.choice_values == [["all", "section", "reset", "cancel"]]
    assert snapshot(ws) == before


def test_menu_redo_one_section(finished_ws):
    ws = finished_ws
    answers = [("What would you like", "section"), ("Which section", "salary"), ("store salary", False)]
    code, p = run_cmd(ws, answers)
    assert code == 0 and p.remaining == 0
    assert load_profile(ws).salary is None
    assert is_setup_complete(ws)


def test_menu_redo_everything_keeps_current_values(finished_ws):
    ws = finished_ws
    before = snapshot(ws)
    # Enter through every section; confirms with default False/True behave as the current files say
    answers = (
        [("What would you like", "all"), ("Use this mapping", ENTER)]
        + [(m, ENTER) for m in ("First name", "Last name", "Email address", "Phone", "Street", "Postal code", "City",
                                "Country", "LinkedIn", "Xing", "Work authorization", "Earliest start", "Notice period",
                                "German level", "English level", "Add another language", "Keep these extra skills",
                                "Add an extra skill")]
        + [("store salary", ENTER), ("Target salary", ENTER), ("Minimum acceptable", ENTER), ("per seniority", ENTER),
                                  ("different amount for jobs", ENTER)]
        + [("'machine-learning-engineer'", ENTER), ("Add another search profile", ENTER)]
        + [(m, ENTER) for m in ("Use Adzuna", "Adzuna app ID", "Adzuna app key", "job-alert emails", "Mail provider",
                                "Email address", "app password", "Folder or label", "alert jobs get their full ad", "AI read",
                                "LinkedIn job", "Indeed job", "StepStone job", "careers pages",
                                "Parallel Search API key", "company apply links", "Use Arbeitnow",
                                "Use Workwise")]
        + [("Test the connections", False)]
        + [(m, ENTER) for m in ("Provider for evaluate", "Model for evaluate", "Provider for write", "Model for write",
                                "Provider for form_fill", "Model for form_fill", "Provider for form_find",
                                "Model for form_find", "Anthropic API key")]
        + [("Test the API key", False)]
        + [("sending mailbox", ENTER), ("same account", ENTER), ("Adjust these limits", ENTER),
           ("Default run mode", ENTER), ("Test the mail login", False)]
        + [("Writing instructions", ENTER), ("Letter language", ENTER), ("writing samples", ENTER)]
    )
    code, p = run_cmd(ws, answers)
    assert code == 0 and p.remaining == 0
    assert snapshot(ws) == before


# ------------------------------------------------------------------------- reset


def test_declined_section_reset_changes_nothing(finished_ws):
    ws = finished_ws
    before = snapshot(ws)
    code, p = run_cmd(ws, [("Reset the 'email' section", False)], reset="email")
    assert code == 0
    assert snapshot(ws) == before
    assert list(ws.backups_dir.iterdir()) == []
    assert "Nothing changed." in p.output


def test_confirmed_section_reset_returns_defaults_and_backs_up(finished_ws):
    ws = finished_ws
    before = snapshot(ws)
    # switch to automatic first so the reset is visible
    ws.config_path.write_text(ws.config_path.read_text().replace("default_mode: review", "default_mode: automatic"))
    code, _ = run_cmd(ws, [("Reset the 'email' section", True)], reset="email")
    assert code == 0
    cfg = load_config(ws)
    assert cfg.mail.sending is None and cfg.run.default_mode == "review"
    assert cfg.mail.alerts is not None  # other sections untouched
    assert "email" not in completed_sections(ws) and not is_setup_complete(ws)
    [backup] = list(ws.backups_dir.iterdir())
    assert backup.name.endswith("-reset-email")
    assert (backup / "config.yaml").read_text() != before["config.yaml"].decode()  # backup has auto_send: true
    assert "default_mode: automatic" in (backup / "config.yaml").read_text()
    assert ws.env_path.read_bytes() == before[".env"]  # secrets are kept on a section reset


@pytest.mark.parametrize("name", SECTION_ORDER)
def test_every_section_can_be_reset(finished_ws, name):
    ws = finished_ws
    code, _ = run_cmd(ws, [(f"Reset the '{name}' section", True)], reset=name)
    assert code == 0 and name not in completed_sections(ws)
    assert load_config(ws) is not None  # config stays loadable after any reset


def test_reset_search_and_documents_values(finished_ws):
    ws = finished_ws
    run_cmd(ws, [("Reset the 'search'", True)], reset="search")
    run_cmd(ws, [("Reset the 'documents'", True)], reset="documents")
    run_cmd(ws, [("Reset the 'salary'", True)], reset="salary")
    assert load_config(ws).search_profiles == []
    assert load_profile(ws).documents == [] and load_profile(ws).salary is None


def test_reset_writing_removes_instructions_after_backup(finished_ws):
    ws = finished_ws
    run_cmd(ws, [("Reset the 'writing'", True)], reset="writing")
    assert not ws.writing_instructions_path.exists()
    [backup] = list(ws.backups_dir.iterdir())
    assert (backup / "writing_instructions.md").read_text() == "Be concise.\nBe direct.\n"


def test_declined_full_reset_changes_nothing(finished_ws):
    ws = finished_ws
    before = snapshot(ws)
    code, _ = run_cmd(ws, [("Reset everything", False)], reset="all")
    assert code == 0
    assert snapshot(ws) == before and list(ws.backups_dir.iterdir()) == []


def test_full_reset_backs_up_restarts_and_keeps_user_data(finished_ws):
    ws = finished_ws
    ws.csv_path.write_text("id;status\n1;sent\n")
    app_dir = ws.applications_dir / "application_acme_dev_1"
    app_dir.mkdir()
    (app_dir / "cover_letter.pdf").write_bytes(b"letter")
    sqlite3.connect(ws.db_path).close()
    db_bytes = ws.db_path.read_bytes()
    docs_before = {p.name: p.read_bytes() for p in ws.documents_dir.iterdir()}
    before = snapshot(ws)

    answers = [("Reset everything", True)] + full_answers()
    code, prompter = run_cmd(ws, answers, reset="all")

    assert code == 0 and prompter.remaining == 0
    [backup] = list(ws.backups_dir.iterdir())
    for name in FILES:
        assert (backup / name).read_bytes() == before[name], name
    # onboarding ran again from the first section and finished
    assert is_setup_complete(ws) and completed_sections(ws) == list(SECTION_ORDER)
    assert ws.csv_path.read_text() == "id;status\n1;sent\n"
    assert (app_dir / "cover_letter.pdf").read_bytes() == b"letter"
    assert ws.db_path.read_bytes() == db_bytes
    assert {p.name: p.read_bytes() for p in ws.documents_dir.iterdir()} == docs_before
    first_questions = [m for _, m in prompter.asked][:2]
    assert "Reset everything" in first_questions[0] and "mapping" in first_questions[1]


def test_full_reset_midway_leaves_user_data_even_if_onboarding_is_cancelled(finished_ws):
    ws = finished_ws
    ws.csv_path.write_text("x")

    class Cancel(ScriptedPrompter):
        def _next(self, kind, message, *, record=True):
            if not self._queue:
                raise SetupAborted()
            return super()._next(kind, message, record=record)

    prompter = Cancel([("Reset everything", True)])
    with use_runtime(prompter, FakeChecks()):
        code = run_setup_command(ws, reset="all")
    assert code == 1
    assert not ws.config_path.exists() and any(ws.backups_dir.iterdir())
    assert ws.csv_path.read_text() == "x" and any(ws.documents_dir.iterdir())


# ------------------------------------------------------------------------ resume


def test_cancelled_wizard_keeps_progress_and_resumes_with_missing_sections(ws):
    class CancelAfterScript(ScriptedPrompter):
        def _next(self, kind, message, *, record=True):
            if not self._queue:
                raise SetupAborted()
            return super()._next(kind, message, record=record)

    first = CancelAfterScript(documents_answers() + profile_answers()[:5])
    with use_runtime(first, FakeChecks()):
        assert run_setup_command(ws) == 1
    assert completed_sections(ws) == ["documents"]

    rest = full_answers()[len(documents_answers()):]
    second = ScriptedPrompter(rest)
    with use_runtime(second, FakeChecks()):
        assert run_setup_command(ws) == 0
    assert second.remaining == 0
    assert completed_sections(ws) == list(SECTION_ORDER)
    assert load_profile(ws).personal.last_name == "Mustermann"


def test_unfinished_section_is_not_recorded(ws):
    for f in ws.documents_dir.iterdir():
        f.unlink()
    code, _ = run_cmd(ws, [("Check the folder again", False)])
    assert code == 1
    assert completed_sections(ws) == [] and not is_setup_complete(ws)
    assert isinstance(ws.root, Path)
