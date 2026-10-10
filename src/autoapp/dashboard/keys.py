"""Single-key input for the viewers (standard library only; Unix terminals)."""

from __future__ import annotations

import os
import select
import sys
import time
from contextlib import AbstractContextManager
from types import TracebackType


class KeyReader(AbstractContextManager["KeyReader"]):
    """Puts the terminal in cbreak mode; ``read(timeout)`` returns one key or None.

    Without a terminal (or on a platform without ``termios``) it never returns a key, so the
    caller just keeps refreshing until its own end condition.
    """

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdin
        self._old = None
        self.enabled = False

    def __enter__(self) -> KeyReader:
        try:
            import termios
            import tty

            if self.stream.isatty():
                fd = self.stream.fileno()
                self._old = termios.tcgetattr(fd)
                tty.setcbreak(fd)
                self.enabled = True
        except (ImportError, OSError, ValueError, AttributeError):
            self.enabled = False
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        if self._old is not None:
            import termios

            termios.tcsetattr(self.stream.fileno(), termios.TCSADRAIN, self._old)
            self._old = None

    def read(self, timeout: float) -> str | None:
        if not self.enabled:
            return None
        ready, _, _ = select.select([self.stream], [], [], timeout)
        if not ready:
            return None
        return os.read(self.stream.fileno(), 1).decode(errors="ignore") or None


class ScriptedKeys(AbstractContextManager["ScriptedKeys"]):
    """Test double: returns the given keys in order, then None."""

    def __init__(self, keys: str = "") -> None:
        self.keys = list(keys)
        self.enabled = True

    def __exit__(self, *exc) -> None:
        return None

    def read(self, timeout: float) -> str | None:
        if self.keys:
            return self.keys.pop(0)
        time.sleep(min(timeout, 0.05))  # behave like a real timeout, without slowing tests down
        return None
