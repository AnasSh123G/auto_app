"""Job-alert email source: IMAP reader, parser registry and per-sender parsers."""

from __future__ import annotations

from autoapp.sources.alerts.excerpt import Redactor
from autoapp.sources.alerts.imap_reader import (
    AlertMailbox,
    EmailFallback,
    FakeMailbox,
    FallbackSkipped,
    ImapAlertMailbox,
    IngestResult,
    UnparsedEmail,
    ingest_alerts,
)
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.alerts.registry import REGISTRY, Parser, ParseResult, parse_email, parser_for

__all__ = [
    "REGISTRY",
    "AlertCard",
    "AlertMailbox",
    "EmailFallback",
    "FakeMailbox",
    "FallbackSkipped",
    "ImapAlertMailbox",
    "IngestResult",
    "ParseResult",
    "Parser",
    "Redactor",
    "UnparsedEmail",
    "ingest_alerts",
    "parse_email",
    "parser_for",
]
