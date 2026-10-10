"""Shared helpers for the alert tests."""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "alerts"


def load_eml(name: str) -> EmailMessage:
    """Parse a fixture from ``tests/fixtures/alerts``."""
    msg = BytesParser(policy=policy.default).parsebytes((FIXTURES / name).read_bytes())
    assert isinstance(msg, EmailMessage)
    return msg


@pytest.fixture
def linkedin_msg() -> EmailMessage:
    return load_eml("linkedin_alert.eml")


@pytest.fixture
def stepstone_msg() -> EmailMessage:
    return load_eml("stepstone_alert.eml")
