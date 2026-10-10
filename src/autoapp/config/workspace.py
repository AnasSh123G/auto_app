"""Workspace location and layout.

All personal state lives under one directory (see design decision 2). Its location
comes from ``--workspace``, then ``$AUTOAPP_WORKSPACE``, then ``./workspace``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_VAR = "AUTOAPP_WORKSPACE"
DEFAULT_DIRNAME = "workspace"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.yaml"

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    @property
    def writing_instructions_path(self) -> Path:
        return self.root / "writing_instructions.md"

    @property
    def form_answers_path(self) -> Path:
        """Your answers to questions in application forms, reused in later forms (see ``assist.answers``)."""
        return self.root / "form_answers.md"

    @property
    def documents_dir(self) -> Path:
        return self.root / "documents"

    @property
    def applications_dir(self) -> Path:
        return self.root / "applications"

    @property
    def csv_path(self) -> Path:
        return self.root / "applications.csv"

    @property
    def db_path(self) -> Path:
        return self.root / "autoapp.db"

    @property
    def browser_profile_dir(self) -> Path:
        return self.root / "browser-profile"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def price_cache_path(self) -> Path:
        """OpenRouter prices fetched from its model list (see ``llm.openrouter_prices``)."""
        return self.root / "openrouter-prices.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"

    def ensure_layout(self) -> None:
        """Create the workspace directories that the program writes into.

        ``documents/`` is created too so the user knows where to put files; it is
        never written to afterwards.
        """
        for d in (
            self.root,
            self.documents_dir,
            self.applications_dir,
            self.logs_dir,
            self.backups_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def load_env(self) -> None:
        """Load ``workspace/.env`` into the process environment without overriding
        variables that are already set (so a real environment always wins)."""
        if self.env_path.exists():
            load_dotenv(self.env_path, override=False)
        elif self.root.parent.name == "test-runs" and (self.root.parent.parent / ".env").exists():
            # A fresh test run's folder holds no secrets of its own: it uses the real workspace's keys.
            load_dotenv(self.root.parent.parent / ".env", override=False)


def resolve_workspace(cli_value: str | os.PathLike[str] | None = None, cwd: Path | None = None) -> Workspace:
    """Resolve the workspace root: CLI option, then env var, then ``./workspace``."""
    if cli_value:
        root = Path(cli_value)
    elif os.environ.get(ENV_VAR):
        root = Path(os.environ[ENV_VAR])
    else:
        root = (cwd or Path.cwd()) / DEFAULT_DIRNAME
    return Workspace(root=root.expanduser().resolve())
