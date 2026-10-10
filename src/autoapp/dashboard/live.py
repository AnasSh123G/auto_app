"""The plain run output and the check whether the live screen can be shown (tasks 14.5, 14.6).

The live screen itself (statistics pinned on top, the log and questions below) is ``dashboard.screen``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from rich.console import Console

from autoapp.dashboard.view import MIN_LIVE_WIDTH
from autoapp.events import RunEvent, format_event


class PlainPrinter:
    """One plain line per feed or quiet event (no screen control characters)."""

    def __init__(self, write: Callable[[str], None] | None = None) -> None:
        self.write = write or (lambda line: print(line, flush=True))

    def __call__(self, e: RunEvent) -> None:
        if e.level != "hidden":
            self.write(format_event(e))


def can_use_live(console: Console, stream=None) -> bool:
    stream = stream or sys.stdout
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        tty = False
    return tty and console.width >= MIN_LIVE_WIDTH and not console.is_dumb_terminal
