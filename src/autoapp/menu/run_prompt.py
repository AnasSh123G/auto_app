"""The question at the start of a run: which mode, which profile (run-modes spec)."""

from __future__ import annotations

from dataclasses import dataclass

from autoapp.config.models import Config
from autoapp.setup.prompts import Prompter

MODE_CHOICES = (
    ("automatic", "Full automatic: send emails that pass the safeguards to employers; prepare form applications"),
    ("review", "Review first: save every email as a draft in your mailbox; prepare form applications"),
    ("test", "Test run: like a real run, but every email comes to you, marked TEST_RUN; nothing reaches employers"),
)
MODE_LABELS = {"automatic": "full automatic", "review": "review first", "test": "test run"}


@dataclass
class RunChoice:
    mode: str
    profile: str | None = None
    dry_run: bool = False


def choose_mode(p: Prompter, config: Config, sent_today: int) -> str:
    """Ask for the mode; automatic needs a yes (default no). Declining asks again."""
    while True:
        mode = p.select("How should this run handle emails?", list(MODE_CHOICES), default=config.run.default_mode)
        if mode != "automatic":
            return mode
        p.say(f"{sent_today} of {config.email.daily_cap} emails sent today.")
        if p.confirm("This will email employers. Continue?", default=False):
            return mode


def choose_run(p: Prompter, config: Config, sent_today: int, *, ask_profile: bool = True) -> RunChoice:
    mode = choose_mode(p, config, sent_today)
    profile = None
    names = [sp.name for sp in config.search_profiles]
    if ask_profile and len(names) > 1:
        picked = p.select("Which search profiles?", [("", "All profiles"), *[(n, n) for n in names]], default="")
        profile = picked or None
    return RunChoice(mode=mode, profile=profile)

