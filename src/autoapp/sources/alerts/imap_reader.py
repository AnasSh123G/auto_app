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
from typing import Any, Protocol

from pydantic import BaseModel, Field

from autoapp.config.models import MailAccount
from autoapp.sources.alerts._mail import from_address
from autoapp.sources.alerts.excerpt import (
    Redactor,
    block_around,
    card_excerpts,
    looks_like_alert,
    redacted_body,
)
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.alerts.registry import parser_for

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class AlertMailbox(Protocol):
    """Source of alert emails: ``(message_id, message)`` pairs from one folder."""

    def list_messages(self, folder: str) -> Iterable[tuple[str, EmailMessage]]: ...


def pick_per_sender(newest_first: Iterable[tuple[str, EmailMessage]], is_processed: Callable[[str], bool],
                    per_sender: int, *, scan: int) -> list[tuple[str, EmailMessage]]:
    """The newest ``per_sender`` unprocessed, alert-looking emails of each known sender, from the ``scan`` newest messages.

    Account and marketing mail (no job link for the platform) and mail from unknown senders are passed over.
    """
    taken: dict[str, int] = {}
    picked: list[tuple[str, EmailMessage]] = []
    for index, (message_id, msg) in enumerate(newest_first):
        if index >= scan:
            break
        parser = parser_for(msg)
        if parser is None or taken.get(parser.name, 0) >= per_sender or is_processed(message_id):
            continue
        if not parser.is_job_alert(msg):
            continue
        taken[parser.name] = taken.get(parser.name, 0) + 1
        picked.append((message_id, msg))
    return picked


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

    def list_per_sender(self, folder: str, is_processed: Callable[[str], bool], per_sender: int,
                        scan: int) -> list[tuple[str, EmailMessage]]:
        """The quick test's sample: see ``pick_per_sender`` (the in-memory list is oldest first)."""
        return pick_per_sender(reversed(list(self.list_messages(folder))), is_processed, per_sender, scan=scan)


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
    ) -> None:
        self.account = account
        self._password = password
        self.ssl_context = ssl_context
        self.since_days = since_days
        self.timeout = timeout

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
            for num in ids:
                fetched = _fetch_message(conn, num, "(BODY.PEEK[])")
                if fetched is not None:
                    yield _message_id(fetched[0], fetched[1]), fetched[0]
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def list_per_sender(self, folder: str, is_processed: Callable[[str], bool], per_sender: int,
                        scan: int) -> list[tuple[str, EmailMessage]]:
        """The quick test's sample: the newest alert-looking email of each known sender, read-only.

        Only the From, Subject and Message-ID headers of the ``scan`` newest messages are fetched (``BODY.PEEK``, so no
        flag is set). A body is fetched only for the newest message of a known sender, and a few more of that sender
        when it turns out to be account or marketing mail (``parser.is_job_alert``).
        """
        conn = self._connect()
        try:
            status, _ = conn.select(_quote(folder), readonly=True)
            if status != "OK":
                raise RuntimeError(f"cannot open IMAP folder {folder!r}")
            criteria: tuple[str, ...] = ("ALL",)
            if self.since_days is not None:
                criteria = ("SINCE", _imap_date(date.today() - timedelta(days=self.since_days)))
            status, data = conn.search(None, *criteria)
            if status != "OK":
                raise RuntimeError("IMAP SEARCH failed")
            ids = data[0].split() if data and data[0] else []
            candidates: dict[str, list[Any]] = {}  # parser name -> message numbers, newest first
            parsers: dict[str, Any] = {}
            for num in reversed(ids[-scan:] if scan > 0 else []):
                fetched = _fetch_message(conn, num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])")
                if fetched is None:
                    continue
                head, _raw = fetched
                parser = parser_for(head)
                if parser is None:
                    continue
                if head["Message-ID"] and is_processed(str(head["Message-ID"]).strip()):
                    continue
                candidates.setdefault(parser.name, []).append(num)
                parsers[parser.name] = parser
            picked: list[tuple[str, EmailMessage]] = []
            for name, nums in candidates.items():
                taken = 0
                for num in nums[:per_sender + _EXTRA_BODIES]:
                    fetched = _fetch_message(conn, num, "(BODY.PEEK[])")
                    if fetched is None:
                        continue
                    msg, raw = fetched
                    message_id = _message_id(msg, raw)
                    if is_processed(message_id) or not parsers[name].is_job_alert(msg):
                        continue
                    picked.append((message_id, msg))
                    taken += 1
                    if taken >= per_sender:
                        break
            return picked
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


# Bodies fetched for one sender beyond the wanted count while looking for an alert among its account mail.
_EXTRA_BODIES = 4


def _fetch_message(conn: imaplib.IMAP4, num: Any, what: str) -> tuple[EmailMessage, bytes] | None:
    status, parts = conn.fetch(num.decode() if isinstance(num, bytes) else num, what)
    if status != "OK" or not parts:
        return None
    raw = next((p[1] for p in parts if isinstance(p, tuple) and len(p) > 1), None)
    if not raw:
        return None
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    return msg, raw


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


class FallbackSkipped(Exception):  # noqa: N818 - not an error: the fallback did not run (cap reached, no key)
    """Raised by a fallback that could not run for this email (extraction cap reached, no model available)."""


# The extraction hook: reads one unparsed email and returns the cards it can name. Cards come back without excerpt or
# message id (ingestion fills both in). It ran when it returns (an empty list means "read it, found nothing"); it did
# not run when it raises ``FallbackSkipped`` or any other exception.
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
    not_alerts: list[str] = Field(default_factory=list)  # known sender, no job link: account mail, marked processed


def ingest_alerts(
    mailbox: AlertMailbox,
    folder: str,
    is_processed: Callable[[str], bool],
    mark_processed: Callable[[str, str, int], None],
    *,
    newest: int | None = None,
    redactor: Redactor | None = None,
    fallback: EmailFallback | None = None,
    fallback_unknown_senders: bool = False,
    per_sender: int | None = None,
    scan: int = 60,
) -> IngestResult:
    """Parse every unprocessed email in ``folder``.

    ``mark_processed(message_id, sender, card_count)`` is called for every email from a known sender that gave at
    least one card. A known sender with zero cards, or a parser that raises, goes to ``fallback`` (when given) and is
    marked processed only after the fallback has run: with the cards it named, or with none and a warning naming
    sender and subject (so a miss is not paid for again in every run). When the fallback is not given, raises, or
    skips the email (``FallbackSkipped``: cap reached), the email gets the same warning and is **not** marked
    processed: it is read again on the next run.
    Emails from unknown senders are counted as unrecognised and left unprocessed so that they are ingested once a
    parser exists; with ``fallback_unknown_senders`` those that read like a job alert go to ``fallback`` too.
    ``newest`` keeps only the last N messages the mailbox lists (oldest first). ``per_sender`` (the quick test) reads
    instead the newest ``per_sender`` alert-looking emails of each known sender, looking at the ``scan`` newest
    messages only.

    An email from a known sender with no job link for its platform (``parser.is_job_alert``) is account or marketing
    mail, not a job alert: it is marked processed (0 cards) and listed in ``not_alerts``, without a fallback call
    or a warning.

    Every card carries ``message_id`` and a redacted ``email_excerpt`` of its block (``redactor`` removes the
    profile's name, address, e-mail and phone, e-mail addresses, tracking links and query strings).
    """
    redactor = redactor or Redactor()
    result = IngestResult()
    seen: set[str] = set()
    sampler = getattr(mailbox, "list_per_sender", None) if per_sender is not None else None
    if per_sender is not None and sampler is not None:
        messages: Iterable[tuple[str, EmailMessage]] = sampler(folder, is_processed, per_sender, scan)
    elif per_sender is not None:
        messages = pick_per_sender(reversed(list(mailbox.list_messages(folder))), is_processed, per_sender, scan=scan)
    else:
        messages = mailbox.list_messages(folder)  # a generator for the real mailbox: read one message at a time
    if newest is not None and per_sender is None:
        messages = list(messages)[-newest:] if newest > 0 else []
    for message_id, msg in messages:
        if message_id in seen or is_processed(message_id):
            result.skipped_processed += 1
            continue
        seen.add(message_id)
        sender = from_address(msg)
        parser = parser_for(msg)
        reason: str | None = None
        cards: list[AlertCard] = []
        if parser is None:
            body = redacted_body(msg, redactor) if fallback is not None and fallback_unknown_senders else ""
            if not (body and looks_like_alert(redactor.redact(str(msg["Subject"] or "")), body)):
                result.unrecognised += 1
                continue
            parser_name, reason = "unknown sender", "no parser for this sender"
        elif not parser.is_job_alert(msg):
            mark_processed(message_id, sender, 0)  # account or marketing mail: nothing to extract, not read again
            result.not_alerts.append(message_id)
            continue
        else:
            parser_name = parser.name
            try:
                cards = parser.parse(msg)
            except Exception as exc:  # a broken parser must not stop the other emails
                reason = f"parser failed ({type(exc).__name__}: {redactor.redact(str(exc))})"
            else:
                if not cards:
                    reason = "no job cards found"
        if reason is not None:
            if parser is not None:
                result.parsers[parser_name] = result.parsers.get(parser_name, 0)
            unparsed = UnparsedEmail(
                message_id=message_id, sender=sender, subject=redactor.redact(str(msg["Subject"] or "")),
                parser=parser.name if parser else None, reason=reason,
                body=body if parser is None else redacted_body(msg, redactor))
            cards, ran = _run_fallback(fallback, unparsed, result)
            if not cards and parser is None and not ran:
                result.unrecognised += 1  # an alert-like email nobody could read: as before, left for a parser
                continue
            if not cards:
                subject = f"in email from {sender} (subject: {unparsed.subject!r})"
                if ran:  # read by the fallback, which found nothing: processed, so it is not read (and paid for) again
                    result.warnings.append(
                        f"{message_id}: {parser_name}: {reason} {subject}; the extraction fallback found no job "
                        "either; the email is marked processed")
                    mark_processed(message_id, sender, 0)
                    result.processed_ids.append(message_id)
                else:
                    result.unprocessed.append(unparsed)
                    result.warnings.append(
                        f"{message_id}: {parser_name}: {reason} {subject}; parser may be outdated; "
                        "the email stays unprocessed and is read again next run")
                continue
            result.llm_extracted += len(cards)
            excerpts = [c.email_excerpt or block_around(unparsed.body, c.title) for c in cards]
        else:
            excerpts = card_excerpts(msg, cards, redactor)
        result.parsers[parser_name] = result.parsers.get(parser_name, 0) + len(cards)
        cards = [c.model_copy(update={"message_id": message_id, "email_excerpt": c.email_excerpt or ex})
                 for c, ex in zip(cards, excerpts, strict=True)]
        result.cards.extend((message_id, card) for card in cards)
        mark_processed(message_id, sender, len(cards))
        result.processed_ids.append(message_id)
    return result


def _run_fallback(fallback: EmailFallback | None, email: UnparsedEmail,
                  result: IngestResult) -> tuple[list[AlertCard], bool]:
    """``(cards, ran)``: ``ran`` is False when there is no fallback, it skipped the email or it failed."""
    if fallback is None:
        return [], False
    try:
        return list(fallback(email)), True
    except FallbackSkipped as why:
        result.warnings.append(f"{email.message_id}: extraction fallback skipped ({why})")
    except Exception as exc:  # noqa: BLE001 - a failing fallback leaves the email unprocessed, like no fallback
        result.warnings.append(f"{email.message_id}: extraction fallback failed ({type(exc).__name__}: {exc})")
    return [], False
