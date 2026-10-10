"""First-run gating through the Typer app (CliRunner) and `require_setup` directly."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.config.workspace import Workspace
from autoapp.setup import gate
from autoapp.setup.checks import FakeChecks
from autoapp.setup.prompts import ScriptedPrompter, is_interactive
from autoapp.setup.wizard import is_setup_complete, use_runtime
from tests.setup.conftest import ANTHROPIC_KEY, DOC_FILES, full_answers

runner = CliRunner()
MESSAGE = "Setup is not complete. Run `autoapp setup` first."


@pytest.fixture(autouse=True)
def _stub_pipeline(monkeypatch):
    """Gate tests only check that the command body is reached; never run the real pipeline."""

    class _Summary:
        ok = False

        def lines(self):
            return ["pipeline stub reached"]

    monkeypatch.setattr("autoapp.steps.pipeline.run_pipeline", lambda ctx, profile=None, **kw: _Summary())


def invoke(ws: Workspace, *args: str):
    return runner.invoke(app, ["-w", str(ws.root), *args])


@pytest.fixture
def bare(tmp_path):
    """A workspace directory that exists but has no setup."""
    return Workspace(root=tmp_path / "bare")


def test_non_interactive_exits_2_with_message_and_does_not_prompt(bare):
    prompter = ScriptedPrompter([])
    with use_runtime(prompter, FakeChecks()):
        result = invoke(bare, "run")
    assert result.exit_code == 2
    assert MESSAGE in result.output
    assert prompter.asked == []
    assert bare.documents_dir.is_dir()  # the workspace layout is ensured


@pytest.mark.parametrize("command", ["search", "run", "evaluate", "prepare", "send", "add", "assist"])
def test_gated_commands_refuse_non_interactively(bare, command):
    assert invoke(bare, command).exit_code == 2


def test_env_var_forces_non_interactive_even_on_a_tty(bare, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    assert is_interactive() is True
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    assert is_interactive() is False
    result = invoke(bare, "run")
    assert result.exit_code == 2 and MESSAGE in result.output


def test_help_and_setup_and_tracking_commands_are_not_gated(bare):
    assert invoke(bare, "--help").exit_code == 0
    assert invoke(bare, "run", "--help").exit_code == 0
    assert invoke(bare, "setup", "--help").exit_code == 0
    result = invoke(bare, "status")
    assert result.exit_code == 0 and MESSAGE not in result.output


def test_interactive_starts_onboarding_before_the_command(ws, monkeypatch):
    monkeypatch.setattr(gate, "is_interactive", lambda: True)
    prompter = ScriptedPrompter(full_answers())
    with use_runtime(prompter, FakeChecks()):
        result = invoke(ws, "run")
    assert prompter.remaining == 0 and is_setup_complete(ws)
    # the command body (still a stub) only ran after the wizard finished
    assert "pipeline stub reached" in result.output and result.exit_code == 1
    assert ANTHROPIC_KEY not in result.output
    assert prompter.asked[0][1].startswith("Use this mapping")


def test_interactive_unfinished_onboarding_stops_the_command(ws, monkeypatch):
    monkeypatch.setattr(gate, "is_interactive", lambda: True)
    for name in DOC_FILES:
        (ws.documents_dir / name).unlink()
    prompter = ScriptedPrompter([("Check the folder again", False)])
    with use_runtime(prompter, FakeChecks()):
        result = invoke(ws, "run")
    assert result.exit_code == 1 and "pipeline stub reached" not in result.output


def test_completed_setup_lets_commands_through(finished_ws):
    result = invoke(finished_ws, "run")
    assert MESSAGE not in result.output
    assert "pipeline stub reached" in result.output  # reached the (stub) command body


def test_gate_follows_the_workspace_of_each_invocation(finished_ws, bare):
    assert "pipeline stub reached" in invoke(finished_ws, "run").output
    assert invoke(bare, "run").exit_code == 2
    assert "pipeline stub reached" in invoke(finished_ws, "run").output


def test_require_setup_is_callable_by_other_commands(bare, finished_ws):
    gate.require_setup(finished_ws, interactive=False)  # complete: returns quietly
    with pytest.raises(typer.Exit) as exc:
        gate.require_setup(bare, interactive=False)
    assert exc.value.exit_code == 2
    assert bare.documents_dir.is_dir()


def test_setup_command_options_through_the_cli(finished_ws):
    prompter = ScriptedPrompter([("What would you like", "cancel")])
    with use_runtime(prompter, FakeChecks()):
        assert invoke(finished_ws, "setup").exit_code == 0
    assert invoke(finished_ws, "setup", "--section", "bogus").exit_code == 2
    prompter = ScriptedPrompter([("Reset the 'email'", False)])
    with use_runtime(prompter, FakeChecks()):
        assert invoke(finished_ws, "setup", "--reset", "email").exit_code == 0
    assert invoke(finished_ws, "setup").exit_code == 2  # no terminal and no injected prompts
