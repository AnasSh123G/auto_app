"""Build the ``email.eml`` draft for email applications (design decision 11)."""

from __future__ import annotations

import email
import mimetypes
import re
from collections.abc import Iterable
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


def load_message(folder: Path) -> EmailMessage:
    """Read the ``email.eml`` of an application folder."""
    return email.message_from_bytes((folder / "email.eml").read_bytes(), policy=policy.default)  # type: ignore[return-value]


def _without_arbeitsagentur_links(body: str) -> str:
    """Remove board URLs from every email draft, including drafts rebuilt from an old message."""
    return re.sub(r"https?://(?:[a-z0-9-]+\.)*arbeitsagentur\.de\S*", "", body, flags=re.I).strip()


def _unique_attachments(primary: Path, extras: Iterable[Path]) -> list[Path]:
    seen = {primary.resolve()}
    unique = [primary]
    for path in extras:
        resolved = path.resolve()
        if resolved in seen:
            continue
        if not path.is_file():
            raise FileNotFoundError(f"email attachment is missing: {path}")
        seen.add(resolved)
        unique.append(path)
    return unique


def build_application_email(
    *,
    sender_name: str,
    sender_email: str,
    to: str,
    subject: str,
    body: str,
    attachment: Path,
    extra_attachments: Iterable[Path] = (),
    message_id: str | None = None,
) -> EmailMessage:
    """UTF-8 message with the PDF attached under the attachment's file name (default Bewerbungsmappe.pdf)."""
    user, _, domain = sender_email.rpartition("@")
    msg = EmailMessage()
    msg["From"] = Address(display_name=sender_name, username=user, domain=domain)
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id or make_msgid(domain=domain)
    msg.set_content(_without_arbeitsagentur_links(body), charset="utf-8", cte="quoted-printable")
    for path in _unique_attachments(attachment, extra_attachments):
        content_type, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def write_eml(msg: EmailMessage, path: Path) -> None:
    """Write the message as RFC 822 bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msg.as_bytes())
