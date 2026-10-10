"""Command-line entry point.

Every subcommand is registered here; the implementations live in their own modules
and are filled in task by task.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from autoapp.config.workspace import Workspace, resolve_workspace

if TYPE_CHECKING:
    from autoapp.config.models import Config

app = typer.Typer(
    name="autoapp",
    help="Find jobs, evaluate them against your CV, prepare applications and track them. "
    "Run `autoapp` alone in a terminal for the menu.",
    no_args_is_help=False,
    add_completion=False,
)


@dataclass
class AppContext:
    """Shared state for all subcommands, stored on ``ctx.obj``."""

    workspace: Workspace


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory (default: $AUTOAPP_WORKSPACE, then ./workspace).",
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Without a command: print this help instead of opening the menu."
    ),
) -> None:
    ws = resolve_workspace(workspace)
    ws.load_env()
    ctx.obj = AppContext(workspace=ws)
    from autoapp.setup.gate import install_gate

    install_gate(ctx, ws)
    if ctx.invoked_subcommand is None:
        _open_menu_or_help(ctx, ws, non_interactive)


def _open_menu_or_help(ctx: typer.Context, ws: Workspace, non_interactive: bool) -> None:
    """``autoapp`` alone: the main menu in a terminal (after the first-run wizard), help otherwise."""
    from autoapp.setup.gate import require_setup
    from autoapp.setup.prompts import is_interactive
    from autoapp.setup.wizard import has_injected_prompter

    if non_interactive or not (is_interactive() or has_injected_prompter()):
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)
    require_setup(ws, interactive=True)
    from autoapp.menu.main import main_menu

    main_menu(ctx)
    raise typer.Exit(code=0)


def get_context(ctx: typer.Context) -> AppContext:
    obj = ctx.find_object(AppContext)
    if obj is None:  # pragma: no cover - the callback always sets it
        raise RuntimeError("CLI context missing")
    return obj


@app.command()
def setup(
    ctx: typer.Context,
    section: str | None = typer.Option(
        None, "--section", help="Redo only this section (documents, profile, salary, search, sources, llm, email, writing)."
    ),
    reset: str | None = typer.Option(
        None, "--reset", help="Reset a section to its defaults, or `all` to back up and start over."
    ),
) -> None:
    """Run the onboarding wizard, or redo/reset a section."""
    from autoapp.setup.wizard import run_setup_command

    code = run_setup_command(get_context(ctx).workspace, section=section, reset=reset)
    if code:
        raise typer.Exit(code=code)


def load_config_or_default(ws: Workspace) -> Config:
    """The workspace config, or defaults before setup has written one.

    Invalid configuration prints every error and exits with code 2.
    """
    from autoapp.config.models import Config, ConfigError, load_config

    if not ws.config_path.exists():
        return Config()
    try:
        return load_config(ws)
    except ConfigError as err:
        for line in err.errors:
            typer.echo(f"error: {line}", err=True)
        raise typer.Exit(code=2) from err


def _document_warnings(ws: Workspace) -> list[str]:
    """Files in documents/ that profile.yaml does not declare (they are never attached)."""
    if not ws.profile_path.exists():
        return []
    from autoapp.config.models import ConfigError, load_profile
    from autoapp.documents import DocumentError, load_documents

    try:
        docs = load_documents(ws, load_profile(ws))
    except (ConfigError, DocumentError) as err:
        return [f"documents: {err}"]
    if not docs.unmapped:
        return []
    return ["Files in documents/ not declared in profile.yaml (never attached): " + ", ".join(docs.unmapped)]


def _open_synced(ctx: typer.Context) -> sqlite3.Connection:
    """Open the workspace DB and import the user's CSV edits (``status``, ``notes``)."""
    from autoapp.db.schema import open_db
    from autoapp.tracking.csv_export import sync_csv

    ws = get_context(ctx).workspace
    tracking = load_config_or_default(ws).tracking
    conn = open_db(ws.db_path)
    for warning in sync_csv(conn, ws.csv_path, tracking.delimiter):
        typer.echo(f"warning: {warning}", err=True)
    return conn


@app.command()
def status(ctx: typer.Context) -> None:
    """Show counts per status, today's sends, the review queue and costs."""
    from rich.console import Console

    from autoapp.llm.cost import PriceTable
    from autoapp.tracking.commands import render_status, status_summary

    workspace = get_context(ctx).workspace
    prices = PriceTable.for_workspace(workspace, load_config_or_default(workspace).llm.prices)
    conn = _open_synced(ctx)
    try:
        Console().print(render_status(status_summary(conn, prices=prices)))
        for line in _document_warnings(get_context(ctx).workspace):
            typer.echo(line)
    finally:
        conn.close()


@app.command(name="list")
def list_jobs(
    ctx: typer.Context,
    status: str = typer.Option(..., "--status", "-s", help="Status to list, e.g. review or sent."),
) -> None:
    """List jobs with a given status."""
    from rich.console import Console

    from autoapp.tracking.commands import list_by_status, render_jobs

    conn = _open_synced(ctx)
    try:
        try:
            jobs = list_by_status(conn, status)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from None
        Console().print(render_jobs(jobs, title=f"Jobs with status {status}"))
    finally:
        conn.close()


@app.command()
def mark(
    ctx: typer.Context,
    job_id: str = typer.Argument(..., help="Job id (see `list`)."),
    status: str = typer.Argument(..., help="New status."),
    note: str | None = typer.Option(None, "--note", help="Note stored with the job and shown in the CSV."),
) -> None:
    """Change a job's status manually."""
    from autoapp.tracking.commands import mark as mark_job
    from autoapp.tracking.csv_export import export_csv

    ws = get_context(ctx).workspace
    conn = _open_synced(ctx)
    try:
        try:
            new_status = mark_job(conn, job_id, status, note)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from None
        except KeyError:
            typer.echo(f"error: no job with id {job_id!r}", err=True)
            raise typer.Exit(code=1) from None
        tracking = load_config_or_default(ws).tracking
        export_csv(conn, ws.csv_path, tracking.delimiter, include_skipped=tracking.include_skipped)
    finally:
        conn.close()
    typer.echo(f"{job_id}: status is now {new_status.value}")


# Pipeline commands live in their own module; importing it registers them on ``app``.
from autoapp import commands  # noqa: E402,F401
