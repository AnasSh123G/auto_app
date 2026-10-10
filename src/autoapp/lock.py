"""Single-instance workspace lock (``fcntl.flock`` on ``workspace/.lock``)."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager

from autoapp.config.workspace import Workspace


class WorkspaceBusy(RuntimeError):
    """Another run holds the workspace lock."""


@contextmanager
def workspace_lock(ws: Workspace) -> Iterator[None]:
    """Hold an exclusive, non-blocking lock on the workspace for the ``with`` block.

    Raises ``WorkspaceBusy`` immediately if another run is active. The OS releases
    the lock if the process dies, so a crash never leaves a stale lock.
    """
    ws.lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ws.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise WorkspaceBusy(
                f"Another autoapp run is active in {ws.root} (lock file {ws.lock_path}). "
                "Wait for it to finish and try again."
            ) from None
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
