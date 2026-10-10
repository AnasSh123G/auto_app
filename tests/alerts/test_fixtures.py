"""The anonymised fixtures must not leak personal data and must stay parseable."""

from __future__ import annotations

import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

import pytest

from tests.alerts.conftest import FIXTURES

NAMES = ["linkedin_alert.eml", "stepstone_alert.eml", "linkedin_zero.eml", "stepstone_zero.eml"]
SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _raw(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8", "replace")


def _decoded(name: str) -> str:
    """Headers plus decoded text parts, so quoted-printable line breaks cannot hide a match."""
    msg = BytesParser(policy=policy.default).parsebytes((FIXTURES / name).read_bytes())
    parts = [str(v) for _, v in msg.items()]
    for part in msg.walk():
        if part.get_content_maintype() == "text":
            parts.append(part.get_content())
    return "\n".join(parts)


def _flat(name: str) -> str:
    """Raw file plus the decoded view."""
    return _raw(name) + "\n" + _decoded(name)


@pytest.mark.parametrize("name", NAMES)
def test_parses_with_stdlib_email(name: str) -> None:
    msg = BytesParser(policy=policy.default).parsebytes((FIXTURES / name).read_bytes())
    assert msg["From"] and msg["Subject"] and msg["Message-ID"]
    assert "example.com" in msg["Message-ID"]
    assert parseaddr(str(msg["To"]))[1] == "max.mustermann@example.com"
    body = msg.get_body(("plain",))
    assert body is not None and body.get_content().strip()


@pytest.mark.parametrize("name", NAMES)
def test_no_personal_addresses_or_tokens(name: str) -> None:
    text = _flat(name)
    for needle in ("@gmail.com", "@outlook.com", "magicLink", "otpToken", "midToken", "refId="):
        assert needle not in text, needle
    # Tracking headers are stripped.
    header_block = _raw(name).split("\n\n", 1)[0].lower()
    for header in ("received", "dkim-signature", "arc-", "authentication-results", "x-", "delivered-to"):
        assert not re.search(rf"^{header}", header_block, re.MULTILINE), header


@pytest.mark.parametrize("name", NAMES)
def test_only_dummy_tracking_links(name: str) -> None:
    text = _decoded(name)
    for match in re.finditer(r"click\.stepstone\.de/f/a/(\S+)", text):
        assert match.group(1).startswith("DUMMY"), match.group(0)
    for match in re.finditer(r"https://www\.linkedin\.com/\S*\?(\S+)", text):
        assert match.group(1).startswith("trackingId=DUMMY"), match.group(0)


@pytest.mark.skipif(not SAMPLES.exists() or not list(SAMPLES.glob("*.eml")), reason="samples/ not present")
@pytest.mark.parametrize("name", NAMES)
def test_real_name_absent(name: str) -> None:
    names: set[str] = set()
    for path in SAMPLES.glob("*.eml"):
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        display, address = parseaddr(str(msg["To"] or ""))
        names.update(p for p in re.split(r"\s+", display) if len(p) >= 3)
        local = address.split("@")[0]
        if len(local) >= 4:
            names.add(local)
    assert names, "no recipient name found in samples"
    text = _flat(name).lower()
    for part in names:
        assert part.lower() not in text, "real name or address leaked into a fixture"
