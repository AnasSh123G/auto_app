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


def unreadable_linkedin() -> EmailMessage:
    """A LinkedIn alert that links a job but in a layout the parser does not know: it finds no card in it."""
    msg = load_eml("linkedin_zero.eml")
    body = ("Your job alert for Machine Learning Engineer in Germany\n"
            "One new job: https://www.linkedin.com/jobs/view/4000000001/?trackingId=DUMMY\n")
    del msg["Content-Transfer-Encoding"]
    msg.set_content(body)
    return msg


def account_mail(sender: str, text: str, mid: str = "<account@x>") -> EmailMessage:
    """Account or marketing mail from an alert platform: no job link in it."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Please confirm your email address"
    msg["Message-ID"] = mid
    msg.set_content(text)
    return msg
