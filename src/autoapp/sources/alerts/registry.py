"""Parser registry: picks a parser from the From address only, never the recipient."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Protocol

from pydantic import BaseModel, Field

from autoapp.sources.alerts._mail import from_address
from autoapp.sources.alerts.models import AlertCard


class Parser(Protocol):
    """An alert-email parser for one sender."""

    name: str

    def handles(self, from_addr: str) -> bool:
        """True if this parser handles mail from the (lower-cased) bare address."""
        ...

    def is_job_alert(self, msg: EmailMessage) -> bool:
        """False only when the email is plainly account or marketing mail: it holds no job link for this platform.

        Conservative: when in doubt (for example an email without a readable body) it is an alert.
        """
        ...

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        """Extract all job cards from the message."""
        ...


class ParseResult(BaseModel):
    """Outcome of parsing one email. ``parser_name`` is None for unknown senders."""

    parser_name: str | None = None
    cards: list[AlertCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _build_registry() -> list[Parser]:
    from autoapp.sources.alerts.indeed import IndeedParser
    from autoapp.sources.alerts.linkedin import LinkedInParser
    from autoapp.sources.alerts.stepstone import StepStoneParser
    from autoapp.sources.alerts.workwise import WorkwiseParser

    return [LinkedInParser(), StepStoneParser(), IndeedParser(), WorkwiseParser()]


REGISTRY: list[Parser] = _build_registry()


def parser_for(msg: EmailMessage) -> Parser | None:
    """The first registered parser whose ``handles`` accepts the From address."""
    addr = from_address(msg)
    if not addr:
        return None
    return next((p for p in REGISTRY if p.handles(addr)), None)


def parse_email(msg: EmailMessage) -> ParseResult:
    """Parse one email. Known sender with zero cards yields a 'parser may be outdated' warning."""
    parser = parser_for(msg)
    if parser is None:
        return ParseResult(parser_name=None)
    cards = parser.parse(msg)
    warnings: list[str] = []
    if not cards:
        warnings.append(
            f"{parser.name}: no job cards found in email from {from_address(msg)}; parser may be outdated"
        )
    return ParseResult(parser_name=parser.name, cards=cards, warnings=warnings)
