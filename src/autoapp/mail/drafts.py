"""Drafts in the user's own mailbox over IMAP (email-drafts spec, design decision 4).

* The Drafts folder is found by its special-use attribute (RFC 6154), because its name is
  localised (Gmail in German: ``[Gmail]/Entwürfe``); then ``Drafts`` and ``[Gmail]/Drafts``.
  ``mail.sending.drafts_folder`` wins when set.
* A draft is added with ``APPEND`` and the ``\\Draft`` flag; nothing is ever sent from here.
* Before adding, the folder is searched for the Message-ID, so a draft whose confirmation was
  lost is found again instead of duplicated.
* Sent detection looks for the same recipient and subject in the Sent folder after the draft
  was saved (webmail usually gives a sent draft a new Message-ID).
"""

from __future__ import annotations

import base64
import email
import imaplib
import re
import ssl
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Protocol

from autoapp.config.models import MailAccount
from autoapp.mail.connection import MailAuthError, MailSendError, imap_endpoint, ssl_context_for

DRAFT_FALLBACKS = ("Drafts", "[Gmail]/Drafts")
SENT_FALLBACKS = ("Sent", "[Gmail]/Sent Mail")
_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


class DraftFolderNotFound(MailSendError):
    """No Drafts folder could be found; ``mail.sending.drafts_folder`` must be set."""


# --- modified UTF-7 (RFC 3501 5.1.3) ------------------------------------------------------


def imap_utf7_encode(name: str) -> str:
    out, buf = [], []

    def flush() -> None:
        if buf:
            b64 = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append("&" + b64 + "-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def imap_utf7_decode(name: str) -> str:
    def repl(m: re.Match[str]) -> str:
        body = m.group(1)
        if not body:
            return "&"
        b64 = body.replace(",", "/")
        return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")

    return re.sub(r"&([^-]*)-", repl, name)


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_list_line(line: bytes | tuple) -> tuple[set[str], str] | None:
    """``(attributes, encoded name)`` from one ``LIST`` response line."""
    if isinstance(line, tuple):  # name sent as a literal
        head, literal = line[0], line[1]
        m = _LIST_LINE.match(head.rstrip() + b' ""')
        if not m:
            return None
        return {f.lower() for f in m.group("flags").decode().split()}, literal.decode()
    m = _LIST_LINE.match(line.strip())
    if not m:
        return None
    name = m.group("name").decode()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return {f.lower() for f in m.group("flags").decode().split()}, name


def find_special_folder(lines: Iterable, attribute: str, configured: str | None,
                        fallbacks: tuple[str, ...]) -> str | None:
    """The encoded folder name: the configured one, else by attribute, else a fallback that exists."""
    folders = [f for f in (parse_list_line(ln) for ln in lines if ln) if f is not None]
    names = {name for _, name in folders}
    if configured:
        encoded = imap_utf7_encode(configured)
        return encoded if encoded in names or not names else None
    for attrs, name in folders:
        if attribute.lower() in attrs:
            return name
    return next((f for f in fallbacks if f in names), None)


# --- the store ------------------------------------------------------------------------------


class DraftStore(Protocol):
    def drafts_folder(self) -> str: ...

    def has_message(self, folder: str, message_id: str) -> bool: ...

    def append_draft(self, folder: str, msg: EmailMessage) -> None: ...

    def find_sent(self, recipient: str, subject: str, since: datetime) -> bool: ...

    def close(self) -> None: ...


class ImapDraftStore:
    """Drafts over a real (or injected, for tests) ``imaplib`` connection."""

    def __init__(self, account: MailAccount, password: str, *, imap=None, timeout: float = 30,
                 ssl_context: ssl.SSLContext | None = None) -> None:
        self.account = account
        self._imap = imap
        self._password = password
        self._timeout = timeout
        self._ssl = ssl_context
        self._folders: list | None = None

    def _conn(self):
        if self._imap is not None:
            return self._imap
        ep = imap_endpoint(self.account)
        ctx = self._ssl or ssl_context_for(self.account)
        try:
            if ep.tls == "ssl":
                conn = imaplib.IMAP4_SSL(ep.host, ep.port, ssl_context=ctx, timeout=self._timeout)
            else:
                conn = imaplib.IMAP4(ep.host, ep.port, timeout=self._timeout)
                if ep.tls == "starttls":
                    conn.starttls(ssl_context=ctx)
        except (OSError, ssl.SSLError, imaplib.IMAP4.error) as err:
            raise MailSendError(f"could not connect to {ep.host}:{ep.port}: {err}") from err
        try:
            conn.login(self.account.username or self.account.address, self._password)
        except imaplib.IMAP4.error as err:
            raise MailAuthError(f"the IMAP server rejected the login for {self.account.address}") from err
        self._imap = conn
        return conn

    def _list(self) -> list:
        if self._folders is None:
            typ, data = self._conn().list()
            if typ != "OK":
                raise MailSendError("could not list the mailbox folders")
            self._folders = list(data or [])
        return self._folders

    def drafts_folder(self) -> str:
        name = find_special_folder(self._list(), "\\Drafts", self.account.drafts_folder, DRAFT_FALLBACKS)
        if name is None:
            raise DraftFolderNotFound(
                "no Drafts folder found in the sending mailbox; set mail.sending.drafts_folder in config.yaml")
        return name

    def sent_folder(self) -> str | None:
        return find_special_folder(self._list(), "\\Sent", self.account.sent_folder, SENT_FALLBACKS)

    def has_message(self, folder: str, message_id: str) -> bool:
        conn = self._conn()
        typ, _ = conn.select(_quote(folder), readonly=True)
        if typ != "OK":
            raise MailSendError(f"could not open the folder {imap_utf7_decode(folder)}")
        typ, data = conn.search(None, "HEADER", "Message-ID", _quote(message_id))
        return typ == "OK" and bool(data and data[0] and data[0].split())

    def append_draft(self, folder: str, msg: EmailMessage) -> None:
        typ, data = self._conn().append(_quote(folder), r"(\Draft \Seen)",
                                        imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise MailSendError(f"the server did not accept the draft: {data!r}")

    def find_sent(self, recipient: str, subject: str, since: datetime) -> bool:
        folder = self.sent_folder()
        if folder is None:
            return False
        conn = self._conn()
        typ, _ = conn.select(_quote(folder), readonly=True)
        if typ != "OK":
            return False
        day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
        typ, data = conn.search(None, "SINCE", day, "TO", _quote(recipient))
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        for num in ids[-50:]:
            typ, parts = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE TO)])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if typ != "OK" or raw is None:
                continue
            headers = email.message_from_bytes(raw, policy=policy.default)
            got = str(make_header(decode_header(str(headers.get("Subject", ""))))).strip()
            try:
                when = parsedate_to_datetime(str(headers.get("Date")))
            except (TypeError, ValueError):
                when = None
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if got == subject.strip() and (when is None or when >= since - timedelta(minutes=5)):
                return True
        return False

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
