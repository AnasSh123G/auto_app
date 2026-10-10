"""Pytest configuration and fixtures."""

import os
from pathlib import Path

import pytest

# Cache for browser availability check (run at most once per session)
_chromium_available = None


def _check_chromium_available():
    """Check if Playwright's Chromium is available without launching a browser."""
    global _chromium_available

    if _chromium_available is not None:
        return _chromium_available

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            chromium_path = p.chromium.executable_path
            _chromium_available = Path(chromium_path).exists()
    except Exception:
        _chromium_available = False

    return _chromium_available


def pytest_collection_modifyitems(items):
    """Modify test collection to skip live and browser tests based on environment."""
    for item in items:
        # Check for @pytest.mark.live(env="...") marker
        live_marker = item.get_closest_marker("live")
        if live_marker:
            # Get env var name from keyword arg or first positional arg
            env_var = live_marker.kwargs.get("env") or (
                live_marker.args[0] if live_marker.args else None
            )
            if env_var and not os.environ.get(env_var):
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"set {env_var} to run live tests"
                    )
                )

        # Check for @pytest.mark.browser marker
        browser_marker = item.get_closest_marker("browser")
        if browser_marker:
            if not _check_chromium_available():
                item.add_marker(
                    pytest.mark.skip(
                        reason="Playwright Chromium is not installed"
                    )
                )


# --- network guard -------------------------------------------------------------------------
# Tests must never reach the internet. Local addresses (Playwright's driver, the local SMTP
# test server, file:// pages) are fine; tests marked ``live`` may connect anywhere.
import socket  # noqa: E402

_LOCAL = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
_real_connect = socket.socket.connect


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _LOCAL and not host.startswith("/"):
            raise RuntimeError(f"test tried to reach the network ({host}); mock it or mark the test live")
        return _real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(autouse=True)
def _no_price_fetch(monkeypatch):
    """Tests never ask OpenRouter for prices (the fetch itself is tested with a fake HTTP transport)."""
    monkeypatch.setenv("AUTOAPP_NO_PRICE_FETCH", "1")
