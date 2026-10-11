"""Small helpers shared by the alert parsers."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import parseaddr
from html import unescape
from urllib.parse import unquote


def from_address(msg: EmailMessage) -> str:
    """Bare, lower-cased From address (empty string when absent)."""
    return parseaddr(str(msg["From"] or ""))[1].strip().lower()


def html_text(msg: EmailMessage) -> str:
    """Decoded text/html body ('' when the message has none)."""
    part = msg.get_body(("html",))
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""


def plain_text(msg: EmailMessage) -> str:
    """Decoded text/plain body ('' when the message has none)."""
    part = msg.get_body(("plain",))
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""


def link_text(msg: EmailMessage) -> str:
    """Everything of the message a job link can hide in: the html and the plain part, un-escaped and URL-decoded
    (also twice, for a job link wrapped in a tracking address). ``""`` when the message has no readable body."""
    return unescape(unquote(unquote(html_text(msg) + "\n" + plain_text(msg))))
