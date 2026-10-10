from __future__ import annotations

import logging

import pytest

from autoapp.config.workspace import Workspace
from autoapp.logging_setup import setup_run_logging

API_KEY = "my-very-secret-api-value-123"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123_-xyz"


@pytest.fixture
def ws(tmp_path):
    yield Workspace(root=tmp_path)
    logger = logging.getLogger("autoapp")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def _read(path):
    for h in logging.getLogger("autoapp").handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_creates_run_log_file(ws):
    path = setup_run_logging(ws, [])
    assert path.parent == ws.logs_dir and path.name.startswith("run-") and path.suffix == ".log"
    logging.getLogger("autoapp.test").info("hello %s", "world")
    assert "hello world" in _read(path)


def test_masks_configured_secret_and_magic_link(ws, capsys):
    path = setup_run_logging(ws, [API_KEY])
    log = logging.getLogger("autoapp.test")
    log.error("LLM call failed with key %s", API_KEY)
    log.error(f"fetch https://www.stepstone.de/x?a=1&magicLink={JWT}&b=2 failed")
    log.error("also in args %s", f"https://h/?magicLink={JWT}")
    text = _read(path)
    assert API_KEY not in text and JWT not in text
    assert "magicLink=***" in text and "&b=2" in text
    err = capsys.readouterr().err
    assert API_KEY not in err and JWT not in err and "***" in err


def test_masks_key_like_tokens_and_bearer(ws):
    path = setup_run_logging(ws, [])
    log = logging.getLogger("autoapp.test")
    log.warning("anthropic sk-ant-api03-ABCDEFGHIJKLMNOP1234")
    log.warning("gemini AIzaSyA1234567890abcdefghijKLMNOP")
    log.warning("header Authorization: Bearer abcdefghijklmnop1234567890")
    log.warning("short Bearer x is fine, sk-1 too")
    text = _read(path)
    assert "ABCDEFGHIJKLMNOP1234" not in text
    assert "AIzaSyA" not in text
    assert "abcdefghijklmnop1234567890" not in text
    assert "Bearer ***" in text
    assert "short Bearer x is fine, sk-1 too" in text


def test_masks_secret_in_traceback(ws):
    path = setup_run_logging(ws, [API_KEY])
    try:
        raise RuntimeError(f"bad key {API_KEY}")
    except RuntimeError:
        logging.getLogger("autoapp.test").exception("failed")
    text = _read(path)
    assert API_KEY not in text and "RuntimeError" in text


def test_second_setup_does_not_duplicate_handlers(ws):
    setup_run_logging(ws, [])
    path = setup_run_logging(ws, [])
    logging.getLogger("autoapp.test").info("once")
    assert _read(path).count("once") == 1
