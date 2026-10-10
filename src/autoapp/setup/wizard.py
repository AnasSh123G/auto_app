"""The onboarding wizard framework: section registry, progress tracking, entry points.

A ``Section`` bundles its questions (``run``) and its reset. Every section owns specific
keys in ``config.yaml``, ``profile.yaml`` and ``.env`` and writes only those, through
``config.io.update_section`` so comments and hand edits elsewhere survive (design
decisions 2 and 16). The sections themselves live in ``sections.py``.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from dotenv import dotenv_values, set_key
from ruamel.yaml import YAMLError

from autoapp.config.io import _section_span, load_yaml, to_plain, update_section
from autoapp.config.models import ConfigError
from autoapp.config.workspace import Workspace
from autoapp.setup.checks import Checks, RealChecks
from autoapp.setup.prompts import (
    GoBack,
    Prompter,
    QuestionaryPrompter,
    ReplayPrompter,
    SetupAborted,
    Validator,
    is_interactive,
)

SETUP_VERSION = 1
SECTION_ORDER: tuple[str, ...] = (
    "documents",
    "profile",
    "salary",
    "search",
    "sources",
    "llm",
    "email",
    "writing",
)
YAML_HEADER = "# Written by `autoapp setup`. You can edit this file by hand; setup keeps your changes.\n"


# ---------------------------------------------------------------------- context/registry


@dataclass
class SetupContext:
    """What a section needs: the workspace, the prompter and the connection checks."""

    ws: Workspace
    prompter: Prompter
    checks: Checks


@dataclass(frozen=True)
class Section:
    """One wizard section. ``run`` returns False when the user could not finish it."""

    name: str
    title: str
    summary: str
    run: Callable[[SetupContext], bool]
    reset: Callable[[SetupContext], None]
    optional: bool = False
    owns: str = ""  # human-readable list of owned keys


_REGISTRY: dict[str, Section] = {}


def register(section: Section) -> Section:
    _REGISTRY[section.name] = section
    return section


def registry() -> dict[str, Section]:
    """All sections in wizard order (imports ``sections`` on first use)."""
    if not _REGISTRY:
        from autoapp.setup import sections  # noqa: F401  (registers the sections)
    return {name: _REGISTRY[name] for name in SECTION_ORDER}


# ------------------------------------------------------------------- runtime (injection)


@dataclass
class Runtime:
    """Overrides for the real prompter and checks; tests and the gate use ``use_runtime``."""

    prompter: Prompter | None = None
    checks: Checks | None = None


_runtime = Runtime()


@contextmanager
def use_runtime(prompter: Prompter | None = None, checks: Checks | None = None) -> Iterator[Runtime]:
    """Temporarily inject a prompter and/or checks (used by tests; never touches a terminal)."""
    global _runtime
    previous = _runtime
    _runtime = Runtime(prompter=prompter, checks=checks)
    try:
        yield _runtime
    finally:
        _runtime = previous


def has_injected_prompter() -> bool:
    return _runtime.prompter is not None


def make_context(ws: Workspace) -> SetupContext:
    return SetupContext(
        ws=ws,
        prompter=_runtime.prompter or QuestionaryPrompter(),
        checks=_runtime.checks or RealChecks(),
    )


# ------------------------------------------------------------------------- file helpers


def read_yaml(path: Path) -> dict[str, Any]:
    """The file as plain Python data (empty if missing); raises ``ConfigError`` on bad YAML."""
    try:
        data = to_plain(load_yaml(path))
    except YAMLError as exc:
        raise ConfigError([f"{path.name}: invalid YAML: {str(exc).strip().splitlines()[0]}"]) from exc
    return data if isinstance(data, dict) else {}


def write_key(path: Path, key: str, value: Any) -> None:
    """Replace one top-level key, keeping everything else in the file as it is."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(YAML_HEADER, encoding="utf-8")
    update_section(path, key, value)


def drop_key(path: Path, key: str) -> None:
    """Remove one top-level key (and its body); no-op when absent."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    span = _section_span(lines, key)
    if span is None:
        return
    start, end = span
    path.write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")


def write_or_drop(path: Path, key: str, value: Any) -> None:
    """Write ``value``, or remove the key when it is empty."""
    if value in (None, {}, []):
        drop_key(path, key)
    else:
        write_key(path, key, value)


def env_values(ws: Workspace) -> dict[str, str]:
    """Saved ``.env`` values merged over the process environment (the file wins)."""
    saved = {k: v for k, v in dotenv_values(ws.env_path).items() if v} if ws.env_path.exists() else {}
    return {**os.environ, **saved}


def has_secret(ws: Workspace, name: str) -> bool:
    return bool((env_values(ws).get(name) or "").strip())


def set_env(ws: Workspace, name: str, value: str) -> None:
    """Store a secret in ``.env`` only (file mode 0600); never anywhere else."""
    if "\n" in value or "\r" in value:
        raise ValueError("a secret cannot contain line breaks")
    ws.root.mkdir(parents=True, exist_ok=True)
    if not ws.env_path.exists():
        ws.env_path.touch(mode=0o600)
    set_key(str(ws.env_path), name, value, quote_mode="always")
    try:
        ws.env_path.chmod(0o600)
    except OSError:
        pass


def ask_secret(ctx: SetupContext, label: str, env_name: str, *, strip_spaces: bool = False) -> bool:
    """Hidden prompt that writes ``env_name`` to ``.env``. Enter keeps a saved value.

    Returns True when a new value was stored. The value is never echoed or printed.
    """
    saved = has_secret(ctx.ws, env_name)
    suffix = " (a value is saved; press Enter to keep it)" if saved else " (input is hidden)"
    value = ctx.prompter.password(f"{label}{suffix}").strip()
    if strip_spaces:
        value = value.replace(" ", "")
    if not value:
        return False
    set_env(ctx.ws, env_name, value)
    return True


# --------------------------------------------------------------------------- validators


def required(what: str) -> Validator:
    def check(value: str) -> str | None:
        return None if value.strip() else f"{what} is required."

    return check


def integer(what: str, *, minimum: int = 0, maximum: int | None = None, optional: bool = False,
            unit: str = "") -> Validator:
    """A whole number in range; empty is allowed when ``optional`` (meaning "no restriction")."""

    def check(value: str) -> str | None:
        v = value.strip()
        if not v:
            return None if optional else f"{what} is required."
        of_unit = f" of {unit}" if unit else ""
        tail = ", or empty" if optional else ""
        if not re.fullmatch(r"\d+", v):
            return f"{what} must be a whole number{of_unit}{tail}; '{v}' is not."
        n = int(v)
        if n < minimum or (maximum is not None and n > maximum):
            span = f"at least {minimum}" if maximum is None else f"between {minimum} and {maximum}"
            return f"{what} must be {span}."
        return None

    return check


def split_list(text: str) -> list[str]:
    """Split a comma/semicolon/newline separated answer into trimmed non-empty items."""
    return [p.strip() for p in re.split(r"[,;\n]", text) if p.strip()]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "profile"


# ------------------------------------------------------------------------------ progress


def completed_sections(ws: Workspace) -> list[str]:
    data = read_yaml(ws.config_path).get("setup") or {}
    done = data.get("completed_sections") if isinstance(data, dict) else None
    return [str(s) for s in done] if isinstance(done, list) else []


def _setup_version(ws: Workspace) -> int:
    data = read_yaml(ws.config_path).get("setup") or {}
    try:
        return int(data.get("version", 0)) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        return 0


def missing_sections(ws: Workspace) -> list[str]:
    """Sections that are not recorded as completed, in wizard order."""
    try:
        done = set(completed_sections(ws))
    except ConfigError:
        return list(SECTION_ORDER)
    return [n for n in SECTION_ORDER if n not in done]


def is_setup_complete(ws: Workspace) -> bool:
    try:
        return ws.config_path.exists() and not missing_sections(ws) and _setup_version(ws) >= SETUP_VERSION
    except ConfigError:
        return False


def _save_progress(ws: Workspace, done: list[str]) -> None:
    write_key(
        ws.config_path,
        "setup",
        {"completed_sections": [n for n in SECTION_ORDER if n in done], "version": SETUP_VERSION},
    )


def mark_completed(ws: Workspace, name: str) -> None:
    done = completed_sections(ws)
    if name not in done:
        done.append(name)
    _save_progress(ws, done)


def mark_incomplete(ws: Workspace, name: str) -> None:
    if ws.config_path.exists():
        _save_progress(ws, [n for n in completed_sections(ws) if n != name])


# --------------------------------------------------------------------------- running


def _run_with_back(ctx: SetupContext, section: Section) -> bool:
    """Run a section; "Back" on a yes/no question asks the question before it again."""
    from dataclasses import replace

    replay: list = []
    while True:
        wrapped = ReplayPrompter(ctx.prompter, replay)
        try:
            return section.run(replace(ctx, prompter=wrapped))
        except GoBack:
            replay = wrapped.recorded[:-1]


def run_section(ctx: SetupContext, name: str) -> bool:
    """Run one section; records it as completed when it finishes. Returns whether it finished."""
    section = registry()[name]
    ctx.prompter.say()
    ctx.prompter.say(f"=== {section.title} ===")
    ctx.prompter.say(section.summary)
    finished = _run_with_back(ctx, section)
    if finished:
        mark_completed(ctx.ws, name)
    else:
        ctx.prompter.say(f"The '{name}' section is not finished. Run `autoapp setup --section {name}` later.")
    return finished


def run_onboarding(ctx: SetupContext, *, only_missing: bool = True) -> bool:
    """Run the sections in order (by default only those not completed yet).

    Returns True when setup is complete afterwards. A cancelled prompt keeps the progress
    made so far.
    """
    ctx.ws.ensure_layout()
    names = missing_sections(ctx.ws) if only_missing else list(SECTION_ORDER)
    try:
        for name in names:
            if not run_section(ctx, name):
                break
    except SetupAborted:
        ctx.prompter.say()
        ctx.prompter.say("Setup interrupted. Your answers so far are saved; run `autoapp setup` to continue.")
        return False
    complete = is_setup_complete(ctx.ws)
    if complete:
        ctx.prompter.say()
        ctx.prompter.say("Setup is complete.")
        ctx.prompter.say("Tip: keep the default run mode on review and try a dry run first (`autoapp run --dry-run`).")
    return complete


# ---------------------------------------------------------------------- reset / backup


def _timestamp_dir(ws: Workspace, label: str = "") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
    target = ws.backups_dir / stamp
    n = 2
    while target.exists():
        target = ws.backups_dir / f"{stamp}-{n}"
        n += 1
    return target


def backup_files(ws: Workspace, paths: list[Path], label: str = "") -> Path:
    """Copy the existing files among ``paths`` into ``backups/<timestamp>/``."""
    target = _timestamp_dir(ws, label)
    target.mkdir(parents=True)
    for p in paths:
        if p.exists():
            shutil.copy2(p, target / p.name)
    return target


def reset_section(ctx: SetupContext, name: str) -> bool:
    """Return one section's keys to their defaults after confirmation. Returns True if done."""
    section = registry()[name]
    p = ctx.prompter
    p.say(f"Resetting '{name}' returns these keys to their defaults: {section.owns}")
    p.say("Secrets in .env are kept. A copy of your files goes to backups/ first.")
    if not p.confirm(f"Reset the '{name}' section?", default=False):
        p.say("Nothing changed.")
        return False
    ws = ctx.ws
    ctx.ws.ensure_layout()
    backup = backup_files(ws, [ws.config_path, ws.profile_path, ws.writing_instructions_path], f"reset-{name}")
    section.reset(ctx)
    mark_incomplete(ws, name)
    p.say(f"The '{name}' section is back to its defaults (backup: {backup}).")
    return True


def reset_all(ctx: SetupContext) -> bool:
    """Back up the four user files, delete them and restart onboarding from the first section.
    Returns False when the confirmation was declined.

    Documents, the database, applications and ``applications.csv`` are never touched.
    """
    ws, p = ctx.ws, ctx.prompter
    p.say("This backs up config.yaml, profile.yaml, writing_instructions.md and .env to backups/,")
    p.say("then starts onboarding from scratch. Documents, the database and applications stay untouched.")
    if not p.confirm("Reset everything and start over?", default=False):
        p.say("Nothing changed.")
        return False
    ws.ensure_layout()
    files = [ws.config_path, ws.profile_path, ws.writing_instructions_path, ws.env_path]
    stale_env = list(dotenv_values(ws.env_path)) if ws.env_path.exists() else []
    backup = backup_files(ws, files, "")
    for f in files:
        f.unlink(missing_ok=True)
    for name in stale_env:
        os.environ.pop(name, None)
    p.say(f"Backup written to {backup}")
    run_onboarding(ctx, only_missing=False)
    return True


# ------------------------------------------------------------------------------ menu


def show_menu(ctx: SetupContext) -> None:
    """The menu shown when setup is already complete."""
    p = ctx.prompter
    sections = registry()
    choice = p.select(
        "Setup is complete. What would you like to do?",
        [
            ("all", "Redo everything"),
            ("section", "Redo one section"),
            ("reset", "Reset one section to defaults"),
            ("cancel", "Cancel"),
        ],
        default="cancel",
    )
    if choice == "cancel":
        p.say("Nothing changed.")
        return
    if choice == "all":
        run_onboarding(ctx, only_missing=False)
        return
    names = [(n, f"{n} - {s.title}") for n, s in sections.items()]
    name = p.select("Which section?", names)
    if choice == "section":
        _run_one(ctx, name)
    else:
        reset_section(ctx, name)


def _run_one(ctx: SetupContext, name: str) -> bool:
    try:
        return run_section(ctx, name)
    except SetupAborted:
        ctx.prompter.say("Cancelled. Sections already saved keep their answers.")
        return False


def run_setup_command(ws: Workspace, section: str | None = None, reset: str | None = None) -> int:
    """Implementation of ``autoapp setup``; returns the process exit code."""
    names = list(SECTION_ORDER)
    if section and reset:
        typer.echo("error: use either --section or --reset, not both", err=True)
        return 2
    if section and section not in names:
        typer.echo(f"error: unknown section '{section}'. Sections: {', '.join(names)}", err=True)
        return 2
    if reset and reset != "all" and reset not in names:
        typer.echo(f"error: unknown section '{reset}'. Use one of: {', '.join(names)}, or 'all'", err=True)
        return 2
    if not has_injected_prompter() and not is_interactive():
        typer.echo("error: `autoapp setup` needs an interactive terminal", err=True)
        return 2
    ctx = make_context(ws)
    try:
        ws.ensure_layout()
        if reset == "all":
            confirmed = reset_all(ctx)
            return 0 if not confirmed or is_setup_complete(ws) else 1
        if reset:
            reset_section(ctx, reset)
            return 0
        if section:
            return 0 if _run_one(ctx, section) else 1
        if is_setup_complete(ws):
            show_menu(ctx)
            return 0
        return 0 if run_onboarding(ctx) else 1
    except ConfigError as err:
        for line in err.errors:
            typer.echo(f"error: {line}", err=True)
        return 2
    except SetupAborted:
        ctx.prompter.say("Cancelled.")
        return 1
