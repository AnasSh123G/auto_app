"""Build the ``email.eml`` draft for email applications (design decision 11)."""

from __future__ import annotations

from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


def build_application_email(
    *,
    sender_name: str,
    sender_email: str,
    to: str,
    subject: str,
    body: str,
    attachment: Path,
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
    msg.set_content(body, charset="utf-8", cte="quoted-printable")
    msg.add_attachment(
        attachment.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=attachment.name,
    )
    return msg


def write_eml(msg: EmailMessage, path: Path) -> None:
    """Write the message as RFC 822 bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msg.as_bytes())
