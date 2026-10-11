"""Read-only IMAP access to the alert folder and the ingestion loop on top of it.

The mailbox is never modified: the folder is opened with ``EXAMINE``, messages are fetched with
``BODY.PEEK[]`` (does not set ``\\Seen``) and nothing is stored, copied, moved or expunged.
Processed emails are tracked by Message-ID in the database instead.
"""

from __future__ import annotations

import hashlib
import imaplib
import ssl
from collections.abc import Callable, Iterable, Iterator
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Protocol

from pydantic import BaseModel, Field

from autoapp.config.models import MailAccount
from autoapp.sources.alerts._mail import from_address
from autoapp.sources.alerts.excerpt import EXCERPT_CHARS, Redactor, card_excerpts, redacted_body
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.alerts.registry import parser_for

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class AlertMailbox(Protocol):
    """Source of alert emails: ``(message_id, message)`` pairs from one folder."""

    def list_messages(self, folder: str) -> Iterable[tuple[str, EmailMessage]]: ...


class FakeMailbox:
    """In-memory mailbox for tests. Pass a list (served for any folder) or ``{folder: list}``."""

    def __init__(
        self, messages: Iterable[tuple[str, EmailMessage]] | dict[str, list[tuple[str, EmailMessage]]]
    ) -> None:
        self._messages = messages if isinstance(messages, dict) else list(messages)

    def list_messages(self, folder: str) -> Iterable[tuple[str, EmailMessage]]:
        if isinstance(self._messages, dict):
            return list(self._messages.get(folder, []))
        return list(self._messages)


def _imap_date(day: date) -> str:
    """IMAP date literal (``01-Oct-2026``) without depending on the locale."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _quote(folder: str) -> str:
    if "\r" in folder or "\n" in folder:
        raise ValueError("invalid folder name")
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapAlertMailbox:
    """Read-only IMAP reader (stdlib ``imaplib``) driven by a ``MailAccount``.

    Presets: ``gmail`` is imap.gmail.com:993 over SSL, ``proton_bridge`` is 127.0.0.1:1143 with
    STARTTLS (pass an ``ssl_context`` that pins the Bridge certificate), ``custom`` takes host,
    port and TLS mode from the account. Explicit account fields override a preset's defaults.
    """

    def __init__(
        self,
        account: MailAccount,
        password: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
        since_days: int | None = None,
        timeout: float = 30.0,
        max_messages: int | None = None,
    ) -> None:
        self.account = account
        self._password = password
        self.ssl_context = ssl_context
        self.since_days = since_days
        self.timeout = timeout
        self.max_messages = max_messages  # only the newest N messages are fetched (the quick test)

    def endpoint(self) -> tuple[str, int, str]:
        """Resolved ``(host, port, tls)`` with ``tls`` one of ``ssl``, ``starttls``, ``none``."""
        acc = self.account
        if acc.preset == "gmail":
            host, port, tls = "imap.gmail.com", 993, "ssl"
        elif acc.preset == "proton_bridge":
            host, port, tls = "127.0.0.1", 1143, "starttls"
        else:
            host, port, tls = acc.imap_host or "", 993, "ssl"
        host = acc.imap_host or host
        tls = acc.imap_tls or tls
        default_port = {"ssl": 993}.get(tls, 143) if acc.preset == "custom" else port
        return host, acc.imap_port or default_port, tls

    def _connect(self) -> imaplib.IMAP4:
        host, port, tls = self.endpoint()
        if tls == "ssl":
            ctx = self.ssl_context or ssl.create_default_context()
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=self.timeout)
        else:
            conn = imaplib.IMAP4(host, port, timeout=self.timeout)
            if tls == "starttls":
                conn.starttls(ssl_context=self.ssl_context or ssl.create_default_context())
        conn.login(self.account.username or self.account.address, self._password)
        return conn

    def list_messages(self, folder: str) -> Iterator[tuple[str, EmailMessage]]:
        """Yield ``(message_id, message)`` for every message in ``folder``. Strictly read-only."""
        conn = self._connect()
        try:
            status, _ = conn.select(_quote(folder), readonly=True)  # readonly=True sends EXAMINE
            if status != "OK":
                raise RuntimeError(f"cannot open IMAP folder {folder!r}")
            criteria: tuple[str, ...] = ("ALL",)
            if self.since_days is not None:
                criteria = ("SINCE", _imap_date(date.today() - timedelta(days=self.since_days)))
            status, data = conn.search(None, *criteria)
            if status != "OK":
                raise RuntimeError("IMAP SEARCH failed")
            ids = data[0].split() if data and data[0] else []
            if self.max_messages is not None:
                ids = ids[-self.max_messages:] if self.max_messages > 0 else []  # SEARCH lists the oldest first
            for num in ids:
                status, parts = conn.fetch(num.decode() if isinstance(num, bytes) else num, "(BODY.PEEK[])")
                if status != "OK" or not parts:
                    continue
                raw = next((p[1] for p in parts if isinstance(p, tuple) and len(p) > 1), None)
                if not raw:
                    continue
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                assert isinstance(msg, EmailMessage)
                yield _message_id(msg, raw), msg
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def _message_id(msg: EmailMessage, raw: bytes) -> str:
    mid = str(msg["Message-ID"] or "").strip()
    return mid or "no-message-id:" + hashlib.sha1(raw).hexdigest()


class UnparsedEmail(BaseModel):
    """An email from a known sender that gave no card (or broke its parser): what the LLM fallback gets to read.

    ``body`` is the whole email, already redacted; nothing personal or tracking-related is in it.
    """

    message_id: str
    sender: str
    subject: str  # redacted
    parser: str | None = None
    reason: str  # "no job cards found" or "parser failed (<error>)"
    body: str = ""


# The extraction hook (T4's fallback): reads one unparsed email and returns the cards it can name. Cards come back
# without excerpt or message id (ingestion fills both in). An empty list, or an exception, means "found nothing".
EmailFallback = Callable[[UnparsedEmail], list[AlertCard]]


class IngestResult(BaseModel):
    """What one ingestion pass found."""

    cards: list[tuple[str, AlertCard]] = Field(default_factory=list)
    unrecognised: int = 0
    skipped_processed: int = 0
    warnings: list[str] = Field(default_factory=list)
    processed_ids: list[str] = Field(default_factory=list)
    parsers: dict[str, int] = Field(default_factory=dict)  # parser name -> cards found, for the quick test
    unprocessed: list[UnparsedEmail] = Field(default_factory=list)  # known sender, no card: left for the next run
    llm_extracted: int = 0  # cards that only the fallback found


def ingest_alerts(
    mailbox: AlertMailbox,
    folder: str,
    is_processed: Callable[[str], bool],
    mark_processed: Callable[[str, str, int], None],
    *,
    newest: int | None = None,
    redactor: Redactor | None = None,
    fallback: EmailFallback | None = None,
) -> IngestResult:
    """Parse every unprocessed email in ``folder``.

    ``mark_processed(message_id, sender, card_count)`` is called only for emails from a known sender that gave at
    least one card. A known sender with zero cards, or a parser that raises, is **not** marked processed: it gets a
    warning naming sender and subject and is read again on the next run. When ``fallback`` is given it is tried
    first and the email is marked processed only if it names a card; with it, a miss stays a warning.
    Emails from unknown senders are counted as unrecognised and left unprocessed so that they are ingested once a
    parser exists. ``newest`` keeps only the last N messages the mailbox lists (oldest first).

    Every card carries ``message_id`` and a redacted ``email_excerpt`` of its block (``redactor`` removes the
    profile's name, address, e-mail and phone, e-mail addresses, tracking links and query strings).
    """
    redactor = redactor or Redactor()
    result = IngestResult()
    seen: set[str] = set()
    messages = mailbox.list_messages(folder)  # a generator for the real mailbox: read one message at a time
    if newest is not None:
        messages = list(messages)[-newest:] if newest > 0 else []
    for message_id, msg in messages:
        if message_id in seen or is_processed(message_id):
            result.skipped_processed += 1
            continue
        seen.add(message_id)
        sender = from_address(msg)
        parser = parser_for(msg)
        if parser is None:
            result.unrecognised += 1
            continue
        reason: str | None = None
        cards: list[AlertCard] = []
        try:
            cards = parser.parse(msg)
        except Exception as exc:  # a broken parser must not stop the other emails
            reason = f"parser failed ({type(exc).__name__}: {redactor.redact(str(exc))})"
        else:
            if not cards:
                reason = "no job cards found"
        if reason is not None:
            result.parsers[parser.name] = result.parsers.get(parser.name, 0)
            unparsed = UnparsedEmail(
                message_id=message_id, sender=sender, subject=redactor.redact(str(msg["Subject"] or "")),
                parser=parser.name, reason=reason, body=redacted_body(msg, redactor))
            cards = _run_fallback(fallback, unparsed, result)
            if not cards:
                result.unprocessed.append(unparsed)
                result.warnings.append(
                    f"{message_id}: {parser.name}: {reason} in email from {sender} (subject: {unparsed.subject!r})"
                    "; parser may be outdated; the email stays unprocessed and is read again next run")
                continue
            result.llm_extracted += len(cards)
            excerpts = [unparsed.body[:EXCERPT_CHARS]] * len(cards)
        else:
            excerpts = card_excerpts(msg, cards, redactor)
        result.parsers[parser.name] = result.parsers.get(parser.name, 0) + len(cards)
        cards = [c.model_copy(update={"message_id": message_id, "email_excerpt": c.email_excerpt or ex})
                 for c, ex in zip(cards, excerpts, strict=True)]
        result.cards.extend((message_id, card) for card in cards)
        mark_processed(message_id, sender, len(cards))
        result.processed_ids.append(message_id)
    return result


def _run_fallback(fallback: EmailFallback | None, email: UnparsedEmail, result: IngestResult) -> list[AlertCard]:
    if fallback is None:
        return []
    try:
        return list(fallback(email))
    except Exception as exc:  # noqa: BLE001 - a failing fallback leaves the email unprocessed, like no fallback
        result.warnings.append(f"{email.message_id}: extraction fallback failed ({type(exc).__name__}: {exc})")
        return []
