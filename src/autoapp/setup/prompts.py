"""Prompt abstraction for the wizard.

Sections talk to a ``Prompter`` and never to the terminal directly. ``QuestionaryPrompter``
is the real implementation; ``ScriptedPrompter`` replays canned answers so tests never
touch a terminal.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

Choice = tuple[str, str]  # (value, label)
Validator = Callable[[str], str | None]  # returns an error message, or None when valid

ENTER: None = None  # scripted answer meaning "press Enter" (accept the default)


class SetupAborted(Exception):
    """The user cancelled (Ctrl-C / Esc) while the wizard was asking a question."""


class GoBack(Exception):
    """The user chose "Back" on a yes/no question: the wizard asks the previous question again."""


BACK = object()  # what ``confirm_or_back`` returns for "Back"


def is_interactive() -> bool:
    """True when stdin is a terminal and ``AUTOAPP_NON_INTERACTIVE`` is not set to 1."""
    if os.environ.get("AUTOAPP_NON_INTERACTIVE", "").strip() == "1":
        return False
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


class Prompter(Protocol):
    """Everything a section may ask the user or show to them."""

    def say(self, text: str = "") -> None: ...

    def table(self, title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None: ...

    def text(self, message: str, default: str = "", validate: Validator | None = None) -> str:
        """Free text. Re-asks until ``validate`` accepts the answer."""
        ...

    def password(self, message: str) -> str:
        """Hidden input (never echoed). An empty answer means "keep what is saved"."""
        ...

    def confirm(self, message: str, default: bool = True) -> bool: ...

    def select(self, message: str, choices: Sequence[Choice], default: str | None = None) -> str: ...

    def checkbox(self, message: str, choices: Sequence[Choice], defaults: Sequence[str] = ()) -> list[str]: ...

    def multiline(self, message: str, default: str = "") -> str: ...

    def edit_file(self, path: Path) -> None:
        """Open ``path`` in the user's ``$EDITOR`` and wait."""
        ...


# ------------------------------------------------------------------------- questionary


def _editor_command() -> list[str]:
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if raw:
        return shlex.split(raw)
    for candidate in ("nano", "vi"):
        from shutil import which

        if which(candidate):
            return [candidate]
    raise RuntimeError("no editor found; set $EDITOR")


class QuestionaryPrompter:
    """Terminal prompts with ``questionary``; output through ``rich``."""

    def __init__(self) -> None:
        from rich.console import Console

        self._console = Console()

    @staticmethod
    def _answer(value: Any) -> Any:
        if value is None:  # questionary returns None on Ctrl-C
            raise SetupAborted()
        return value

    def say(self, text: str = "") -> None:
        self._console.print(text, markup=False, highlight=False)

    def table(self, title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
        from rich.table import Table

        t = Table(title=title, title_justify="left")
        for h in headers:
            t.add_column(h)
        for row in rows:
            t.add_row(*[str(c) for c in row])
        self._console.print(t)

    def text(self, message: str, default: str = "", validate: Validator | None = None) -> str:
        import questionary

        def check(value: str) -> bool | str:
            err = validate(value) if validate else None
            return True if err is None else err

        return str(self._answer(questionary.text(message, default=default, validate=check).ask()))

    def password(self, message: str) -> str:
        import questionary

        return str(self._answer(questionary.password(message).ask()))

    def confirm(self, message: str, default: bool = True) -> bool:
        import questionary

        return bool(self._answer(questionary.confirm(message, default=default).ask()))

    def confirm_or_back(self, message: str, default: bool = True) -> Any:
        """Yes / No / Back as single key presses (y, n, b). Returns True, False or ``BACK``."""
        import questionary

        items = [questionary.Choice("Yes", True, shortcut_key="y"), questionary.Choice("No", False, shortcut_key="n"),
                 questionary.Choice("← Back to the previous question", BACK, shortcut_key="b")]
        return self._answer(questionary.select(message, choices=items, default=items[0 if default else 1],
                                               use_shortcuts=True).ask())

    def select(self, message: str, choices: Sequence[Choice], default: str | None = None) -> str:
        import questionary

        items = [questionary.Choice(title=label, value=value) for value, label in choices]
        chosen = next((c for c in items if c.value == default), None)
        return str(self._answer(questionary.select(message, choices=items, default=chosen).ask()))

    def checkbox(self, message: str, choices: Sequence[Choice], defaults: Sequence[str] = ()) -> list[str]:
        import questionary

        items = [questionary.Choice(title=label, value=value, checked=value in defaults) for value, label in choices]
        return list(self._answer(questionary.checkbox(message, choices=items).ask()))

    def multiline(self, message: str, default: str = "") -> str:
        import questionary

        self.say("(Multi-line input: press Esc, then Enter to finish.)")
        return str(self._answer(questionary.text(message, default=default, multiline=True).ask()))

    def edit_file(self, path: Path) -> None:
        subprocess.run([*_editor_command(), str(path)], check=True)


# ---------------------------------------------------------------------------- scripted


class ScriptExhausted(AssertionError):
    """The wizard asked more questions than the script has answers."""


class ScriptedPrompter:
    """Replays canned answers in order; records what was asked and shown.

    Each answer is a plain value, ``ENTER`` (``None``: accept the default), or a
    ``(substring, value)`` pair that also asserts the question text contains the substring.
    Answer types: text/password/multiline ``str``, confirm ``bool``, select a choice value,
    checkbox ``list[str]``, edit_file ``str`` (the new file content). Passwords are never
    recorded anywhere.
    """

    def __init__(self, answers: Iterable[Any] = ()) -> None:
        self._queue: deque[Any] = deque(answers)
        self.asked: list[tuple[str, str]] = []  # (kind, message)
        self.choice_values: list[list[str]] = []  # the offered values of each select question
        self.output: list[str] = []

    # -- helpers

    def feed(self, *answers: Any) -> None:
        self._queue.extend(answers)

    @property
    def remaining(self) -> int:
        return len(self._queue)

    @property
    def shown(self) -> str:
        """Everything said, shown as tables, and asked, as one string (for leak checks)."""
        return "\n".join([*self.output, *(m for _, m in self.asked)])

    def _next(self, kind: str, message: str, *, record: bool = True) -> Any:
        if record:
            self.asked.append((kind, message))
        if not self._queue:
            raise ScriptExhausted(f"no scripted answer left for {kind} question: {message!r}")
        item = self._queue.popleft()
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
            pattern, item = item
            if pattern.lower() not in message.lower():
                raise AssertionError(f"expected a question containing {pattern!r}, got {message!r}")
        return item

    # -- Prompter

    def say(self, text: str = "") -> None:
        self.output.append(text)

    def table(self, title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
        self.output.append(title)
        self.output.append(" | ".join(headers))
        self.output.extend(" | ".join(str(c) for c in row) for row in rows)

    def text(self, message: str, default: str = "", validate: Validator | None = None) -> str:
        while True:
            item = self._next("text", message)
            value = default if item is None else str(item)
            err = validate(value) if validate else None
            if err is None:
                return value
            self.output.append(f"! {err}")

    def password(self, message: str) -> str:
        item = self._next("password", message)
        return "" if item is None else str(item)

    def confirm(self, message: str, default: bool = True) -> bool:
        item = self._next("confirm", message)
        return default if item is None else bool(item)

    def select(self, message: str, choices: Sequence[Choice], default: str | None = None) -> str:
        item = self._next("select", message)
        values = [v for v, _ in choices]
        self.choice_values.append(values)
        if item is None:
            return default if default in values else values[0]
        if item not in values:
            raise AssertionError(f"{item!r} is not one of {values} for {message!r}")
        return str(item)

    def checkbox(self, message: str, choices: Sequence[Choice], defaults: Sequence[str] = ()) -> list[str]:
        item = self._next("checkbox", message)
        if item is None:
            return [v for v, _ in choices if v in defaults]
        values = {v for v, _ in choices}
        bad = [v for v in item if v not in values]
        if bad:
            raise AssertionError(f"{bad} not among the choices for {message!r}")
        return list(item)

    def multiline(self, message: str, default: str = "") -> str:
        item = self._next("multiline", message)
        return default if item is None else str(item)

    def edit_file(self, path: Path) -> None:
        item = self._next("edit", f"edit {path.name}")
        if item is not None:
            path.write_text(str(item), encoding="utf-8")


# ---------------------------------------------------------------------------- going back


class ReplayPrompter:
    """Wraps a prompter for one section run and remembers every answer.

    "Back" on a yes/no question raises ``GoBack``; ``run_section`` then runs the section again,
    replaying the remembered answers (without asking or printing them again) up to the question
    before the one the user wants to change, which is asked for real.
    """

    def __init__(self, inner: Prompter, replay: Sequence[Any] = ()) -> None:
        self._inner = inner
        self._replay = list(replay)
        self.recorded: list[Any] = []

    @property
    def replaying(self) -> bool:
        return len(self.recorded) < len(self._replay)

    def _ask(self, live: Callable[[], Any]) -> Any:
        if self.replaying:
            answer = self._replay[len(self.recorded)]
        else:
            answer = live()
        self.recorded.append(answer)
        return answer

    def say(self, text: str = "") -> None:
        if not self.replaying:
            self._inner.say(text)

    def table(self, title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
        if not self.replaying:
            self._inner.table(title, headers, rows)

    def text(self, message: str, default: str = "", validate: Validator | None = None) -> str:
        return self._ask(lambda: self._inner.text(message, default, validate))

    def password(self, message: str) -> str:
        return self._ask(lambda: self._inner.password(message))

    def confirm(self, message: str, default: bool = True) -> bool:
        def live() -> bool:
            ask = getattr(self._inner, "confirm_or_back", None)
            if ask is None or not self.recorded:  # nothing to go back to in this section
                return self._inner.confirm(message, default)
            answer = ask(message, default)
            if answer is BACK:
                raise GoBack()
            return bool(answer)

        return self._ask(live)

    def select(self, message: str, choices: Sequence[Choice], default: str | None = None) -> str:
        return self._ask(lambda: self._inner.select(message, choices, default))

    def checkbox(self, message: str, choices: Sequence[Choice], defaults: Sequence[str] = ()) -> list[str]:
        return self._ask(lambda: self._inner.checkbox(message, choices, defaults))

    def multiline(self, message: str, default: str = "") -> str:
        return self._ask(lambda: self._inner.multiline(message, default))

    def edit_file(self, path: Path) -> None:
        self._ask(lambda: self._inner.edit_file(path))  # an edit is not opened again on replay
