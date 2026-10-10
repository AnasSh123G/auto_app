"""Per-run log files with secret redaction (automation-pipeline spec)."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from autoapp.config.workspace import Workspace

MASK = "***"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(magicLink=)[^&\s\"'<>]+"), rf"\1{MASK}"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{12,}"), f"Bearer {MASK}"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), MASK),
]

_HANDLER_TAG = "_autoapp_run_handler"


class RedactionFilter(logging.Filter):
    """Masks configured secrets, key-like tokens and ``magicLink=`` values."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        # Longest first so a secret containing another is masked whole; skip trivial values.
        self.secrets = sorted({s for s in (secrets or []) if s and len(s) >= 4}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, MASK)
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        # Merge args into the message first so secrets inside args are masked too.
        record.msg = self.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redact(record.stack_info)
        return True


def setup_run_logging(ws: Workspace, secrets: list[str] | None = None, level: int = logging.INFO) -> Path:
    """Log to ``logs/run-<timestamp>.log`` and the console, both redacted. Returns the log path."""
    ws.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = ws.logs_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.log"
    logger = logging.getLogger("autoapp")
    logger.setLevel(level)
    for old in [h for h in logger.handlers if getattr(h, _HANDLER_TAG, False)]:
        logger.removeHandler(old)
        old.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = RedactionFilter(secrets)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)  # commands print their own summaries; the file gets everything
    for handler in (logging.FileHandler(log_path, encoding="utf-8"), console):
        handler.setFormatter(fmt)
        handler.addFilter(redactor)
        setattr(handler, _HANDLER_TAG, True)
        logger.addHandler(handler)
    return log_path
