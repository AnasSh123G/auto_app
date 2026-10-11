"""First-run gating: onboarding for interactive commands, exit code 2 for non-interactive ones.

``require_setup`` is what commands call; ``install_gate`` is what the CLI callback calls to
guard every command that needs a configured workspace.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer

from autoapp.config.workspace import Workspace
from autoapp.setup.prompts import is_interactive
from autoapp.setup.wizard import is_setup_complete, make_context, run_onboarding

NOT_COMPLETE_MESSAGE = "Setup is not complete. Run `autoapp setup` first."

# Commands that work without a completed setup: the wizard itself and the read-only
# tracking commands, which fall back to default settings (cli.load_config_or_default), and `login`,
# which setup itself suggests, and `reset-data`, which only deletes job data.
UNGATED_COMMANDS = frozenset({"setup", "status", "list", "mark", "export", "login", "reset-data"})


def require_setup(ws: Workspace, interactive: bool) -> None:
    """Return when setup is complete.

    Incomplete and ``interactive``: run the onboarding wizard first (exit 1 if the user does
    not finish it). Incomplete and not ``interactive``: print the message and exit with 2,
    without prompting.
    """
    ws.ensure_layout()
    if is_setup_complete(ws):
        return
    if not interactive:
        typer.echo(NOT_COMPLETE_MESSAGE, err=True)
        raise typer.Exit(code=2)
    ctx = make_context(ws)
    ctx.prompter.say("Setup is not complete, so the onboarding wizard starts first.")
    if not run_onboarding(ctx):
        raise typer.Exit(code=1)
    ws.load_env()  # pick up the keys the wizard just saved


def _read_only(name: str, options: dict[str, Any]) -> bool:
    """Invocations that never write or bootstrap a workspace, so they need no completed setup."""
    if name == "forms":
        return bool(options.get("dry_run") or options.get("audit"))
    if name == "employers":
        acting = bool(options.get("discover") or options.get("website"))
        return bool(options.get("dry_run")) or not acting
    return False


def install_gate(ctx: typer.Context, ws: Workspace) -> None:
    """Guard the invoked subcommand so that it runs ``require_setup`` right before its body.

    The check is attached to the command's callback, not run in the group callback, so
    ``autoapp <command> --help`` still works without a completed setup.
    """
    name = ctx.invoked_subcommand
    if not name or name in UNGATED_COMMANDS:
        return
    command = getattr(ctx.command, "get_command", lambda *_: None)(ctx, name)
    if command is None or command.callback is None:
        return
    original: Callable[..., Any] = getattr(command, "_autoapp_original_callback", command.callback)
    command._autoapp_original_callback = original  # type: ignore[attr-defined]

    def gated(*args: Any, **kwargs: Any) -> Any:
        if not _read_only(name, kwargs):
            require_setup(ws, interactive=is_interactive() and not kwargs.get("non_interactive", False))
        return original(*args, **kwargs)

    command.callback = gated
