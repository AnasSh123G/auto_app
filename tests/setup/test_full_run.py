"""A scripted full run: valid files, secrets only in .env, the default run mode is review."""

from __future__ import annotations

import pytest

from autoapp.config.io import load_yaml
from autoapp.config.models import load_config, load_profile
from autoapp.setup.checks import FakeChecks
from autoapp.setup.prompts import ScriptedPrompter
from autoapp.setup.wizard import (
    SECTION_ORDER,
    SetupContext,
    completed_sections,
    is_setup_complete,
    run_onboarding,
)
from tests.setup.conftest import (
    ANTHROPIC_KEY,
    SECRETS,
    full_answers,
)


@pytest.fixture
def finished(ws):
    prompter = ScriptedPrompter(full_answers())
    checks = FakeChecks()
    ctx = SetupContext(ws=ws, prompter=prompter, checks=checks)
    assert run_onboarding(ctx) is True
    return ws, prompter, checks


def test_full_run_writes_loadable_files(finished):
    ws, prompter, _ = finished
    assert prompter.remaining == 0
    cfg = load_config(ws)
    prof = load_profile(ws)
    assert prof.personal.email == "max@example.com"
    assert prof.personal.postal_code == "10115"
    assert prof.language_levels == {"de": "C1", "en": "B2"}
    assert prof.salary is not None and prof.salary.target_eur == 70000 and prof.salary.min_eur == 60000
    assert [s.name for s in prof.extra_skills] == ["Docker"]
    roles = {d.file: d.role for d in prof.documents}
    assert roles == {
        "Anschreiben_alt.pdf": "writing_sample",
        "Arbeitszeugnis.pdf": "work_reference",
        "Foto.jpg": "photo",
        "Lebenslauf_de.pdf": "cv",
    }
    [sp] = cfg.search_profiles
    assert sp.keywords == ["Machine Learning Engineer", "Data Scientist"]
    assert sp.name == "machine-learning-engineer"
    assert sp.locations is not None and sp.locations[0].radius_km == 30
    assert sp.work_mode == "hybrid" and sp.exclude_agencies is True
    assert cfg.sources.adzuna.enabled and cfg.sources.alerts.folder == "Jobalerts"
    assert cfg.mail.alerts is not None and cfg.mail.alerts.preset == "gmail"
    assert cfg.mail.sending is not None and cfg.mail.sending.address == "me@gmail.com"
    assert cfg.mail.sending.password_env == cfg.mail.alerts.password_env
    assert {cfg.llm.resolve(t)[0] for t in ("evaluate", "write", "form_fill", "form_find")} == {"anthropic"}
    assert ws.writing_instructions_path.read_text() == "Be concise.\nBe direct.\n"


def test_progress_is_recorded(finished):
    ws, _, _ = finished
    assert completed_sections(ws) == list(SECTION_ORDER)
    assert load_config(ws).setup.completed_sections == list(SECTION_ORDER)
    assert is_setup_complete(ws)


def test_enter_through_email_defaults_to_review(finished):
    ws, _, _ = finished
    assert load_config(ws).run.default_mode == "review"


def test_secrets_only_in_env_and_never_shown(finished):
    ws, prompter, checks = finished
    env = ws.env_path.read_text()
    assert ANTHROPIC_KEY in env and "ANTHROPIC_API_KEY" in env
    assert "adzuna-key-SECRET" in env
    assert "abcdefghijklmnop" in env  # spaces of the Gmail app password were removed
    assert oct(ws.env_path.stat().st_mode & 0o777) == "0o600"
    for path in (ws.config_path, ws.profile_path, ws.writing_instructions_path):
        text = path.read_text()
        for secret in SECRETS:
            assert secret not in text, f"{secret} leaked into {path.name}"
    shown = prompter.shown
    for secret in SECRETS:
        assert secret not in shown
    # the checks did receive the stored password, so it is used without being echoed
    assert ("imap", "me@gmail.com", "Jobalerts", "abcdefghijklmnop") in checks.calls


def test_hand_edits_are_defaults_on_rerun(finished):
    ws, _, _ = finished
    text = ws.config_path.read_text().replace("folder: Jobalerts", "folder: MyAlerts  # my own folder")
    ws.config_path.write_text(text)
    prompter = ScriptedPrompter([])
    ctx = SetupContext(ws=ws, prompter=prompter, checks=FakeChecks())
    from autoapp.setup.wizard import run_section

    prompter.feed(
        ("Use Adzuna", None), ("Adzuna app ID", None), ("Adzuna app key", None), ("job-alert emails", None),
        ("Mail provider", None), ("Email address", None), ("app password", None), ("Folder or label", None),
        ("alert jobs get their full ad", None), ("LinkedIn job pages", None), ("Indeed job pages", None),
        ("StepStone job pages", None), ("careers pages", None), ("Parallel Search API key", None),
        ("company apply links", None), ("Use Arbeitnow", None), ("Use Workwise", None),
        ("Test the connections", False),
    )
    assert run_section(ctx, "sources")
    assert load_config(ws).sources.alerts.folder == "MyAlerts"
    assert "# my own folder" in ws.config_path.read_text()
    assert load_yaml(ws.config_path)["sources"]["alerts"]["folder"] == "MyAlerts"
