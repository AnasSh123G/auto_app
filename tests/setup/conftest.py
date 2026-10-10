"""Fixtures for the setup wizard tests: temp workspace, fake prompts and checks, no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoapp.config.workspace import Workspace
from autoapp.llm.settings import API_KEY_ENV
from autoapp.setup.checks import FakeChecks
from autoapp.setup.prompts import ENTER, ScriptedPrompter
from autoapp.setup.wizard import SetupContext

ANTHROPIC_KEY = "sk-ant-SECRET-1234567890"
ADZUNA_ID = "adzuna-id-SECRET"
ADZUNA_KEY = "adzuna-key-SECRET"
MAIL_PASSWORD = "abcdefghijklmnop"
SECRETS = [ANTHROPIC_KEY, ADZUNA_ID, ADZUNA_KEY, MAIL_PASSWORD, "abcd efgh ijkl mnop"]

DOC_FILES = ["Lebenslauf_de.pdf", "Anschreiben_alt.pdf", "Foto.jpg", "Arbeitszeugnis.pdf"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real keys or terminal hints leak into the wizard."""
    for name in [
        *API_KEY_ENV.values(),
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "AUTOAPP_ALERTS_PASSWORD",
        "AUTOAPP_SMTP_PASSWORD",
        "AUTOAPP_NON_INTERACTIVE",
        "AUTOAPP_WORKSPACE",
    ]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(root=tmp_path / "ws")
    w.ensure_layout()
    for name in DOC_FILES:
        (w.documents_dir / name).write_bytes(b"%PDF-1.4 test")
    return w


def make_ctx(ws: Workspace, answers: list[Any], checks: FakeChecks | None = None) -> SetupContext:
    return SetupContext(ws=ws, prompter=ScriptedPrompter(answers), checks=checks or FakeChecks())


@pytest.fixture
def checks() -> FakeChecks:
    return FakeChecks()


def documents_answers() -> list[Any]:
    return [("Use this mapping", True)]


def profile_answers() -> list[Any]:
    return [
        ("First name", "Max"),
        ("Last name", "Mustermann"),
        ("Email address", "not-an-email"),  # rejected, asked again
        ("Email address", "max@example.com"),
        ("Phone", "+49 30 123456"),
        ("Street", "Musterstrasse 1"),
        ("Postal code", "10115"),
        ("City", "Berlin"),
        ("Country", ENTER),
        ("LinkedIn", ENTER),
        ("Xing", ENTER),
        ("Work authorization", "EU citizen"),
        ("Earliest start", ENTER),
        ("Notice period", "3 Monate zum Monatsende"),
        ("German level", "C1"),
        ("English level", "B2"),
        ("Add another language", False),
        ("Add an extra skill", True),
        ("Skill name", "Docker"),
        ("Your level", "basic"),
        ("Where did you use it", "Home server"),
        ("Add an extra skill", False),
    ]


def salary_answers() -> list[Any]:
    return [("store salary", True), ("Target salary", "70000"), ("Minimum acceptable", "60000"), ("per seniority", False),
            ("different amount for jobs", False)]


def search_answers() -> list[Any]:
    return [
        ("keywords", "Machine Learning Engineer, Data Scientist"),
        ("Name for this search", ENTER),
        ("optional filters", True),
        ("Exclude titles", "Internship"),
        ("Exclude companies", ENTER),
        ("Locations", "Berlin"),
        ("radius", "abc"),  # invalid: re-asked
        ("radius", "30"),
        ("Work mode", "hybrid"),
        ("Working time", ENTER),
        ("Contract type", ENTER),
        ("Exclude position types", ENTER),
        ("Experience levels", ENTER),
        ("within this many days", ENTER),
        ("Minimum yearly salary", ENTER),
        ("recruitment agencies", True),
        ("Add another search profile", False),
    ]


def sources_answers(*, test: bool = True) -> list[Any]:
    return [
        ("Use Adzuna", True),
        ("Adzuna app ID", ADZUNA_ID),
        ("Adzuna app key", ADZUNA_KEY),
        ("job-alert emails", True),
        ("Mail provider", "gmail"),
        ("Email address", "me@gmail.com"),
        ("app password", "abcd efgh ijkl mnop"),
        ("Folder or label", ENTER),
        ("LinkedIn job pages", ENTER),
        ("Indeed job pages", ENTER),
        ("StepStone job pages", ENTER),
        ("careers pages", ENTER),
        ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER),
        ("Test the connections", test),
    ]


def llm_answers() -> list[Any]:
    return [
        ("Provider for evaluate", "anthropic"),
        ("Model for evaluate", ENTER),
        ("Provider for write", ENTER),  # the first answer is offered again
        ("Model for write", ENTER),
        ("Provider for form_fill", ENTER),
        ("Model for form_fill", ENTER),
        ("Provider for form_find", ENTER),
        ("Model for form_find", ENTER),
        ("Anthropic API key", ANTHROPIC_KEY),
        ("Test the API key", True),
    ]


def email_enter_answers() -> list[Any]:
    """Press Enter on every question of the email section (alert mailbox already configured)."""
    return [
        ("sending mailbox", ENTER),
        ("same account", ENTER),
        ("Adjust these limits", ENTER),
        ("Default run mode", ENTER),
        ("Test the mail login", ENTER),
    ]


def writing_answers() -> list[Any]:
    return [
        ("Writing instructions", "inline"),
        ("Writing instructions", "Be concise.\nBe direct."),
        ("Letter language", ENTER),
        ("writing samples", ENTER),
    ]


def full_answers() -> list[Any]:
    return (
        documents_answers()
        + profile_answers()
        + salary_answers()
        + search_answers()
        + sources_answers()
        + llm_answers()
        + email_enter_answers()
        + writing_answers()
    )


@pytest.fixture
def finished_ws(ws: Workspace) -> Workspace:
    """A workspace that went through the whole wizard."""
    from autoapp.setup.wizard import run_onboarding

    ctx = make_ctx(ws, full_answers())
    assert run_onboarding(ctx)
    return ws
