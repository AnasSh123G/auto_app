from __future__ import annotations

import pytest

from autoapp.config.workspace import Workspace
from autoapp.lock import WorkspaceBusy, workspace_lock


def test_second_acquisition_fails_immediately(tmp_path):
    ws = Workspace(root=tmp_path / "ws")
    with workspace_lock(ws):
        with pytest.raises(WorkspaceBusy, match="Another autoapp run is active"):
            with workspace_lock(ws):
                pytest.fail("must not be acquired")


def test_released_after_block_and_after_error(tmp_path):
    ws = Workspace(root=tmp_path)
    with workspace_lock(ws):
        pass
    with pytest.raises(RuntimeError, match="boom"):
        with workspace_lock(ws):
            raise RuntimeError("boom")
    with workspace_lock(ws):
        assert ws.lock_path.exists()
