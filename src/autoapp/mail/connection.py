"""Mail connections: presets, TLS (with Proton Bridge certificate pinning), SMTP sending
and copying sent mail into the Sent folder (design decision 11).

Gmail and Proton Bridge store SMTP-sent mail in "Sent" themselves, so only the
``custom`` preset gets an IMAP ``APPEND``.
"""

from __future__ import annotations

import hashlib
import imaplib
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal, Protocol

from autoapp.config.models import MailAccount

Tls = Literal["ssl", "starttls", "none"]
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class MailError(Exception):
    """Base class for mail failures."""


class MailAuthError(MailError):
    """The server rejected the login. Stops all sending for the run."""


class MailSendError(MailError):
    """Any other failure while connecting or sending."""


class MailSentNoCopyError(MailError):
    """The mail WAS sent, but the copy into the Sent folder failed. Never resend on this."""


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    tls: Tls


_SMTP_PRESETS: dict[str, Endpoint] = {
    "gmail": Endpoint("smtp.gmail.com", 465, "ssl"),
    "proton_bridge": Endpoint("127.0.0.1", 1025, "starttls"),
}
_IMAP_PRESETS: dict[str, Endpoint] = {
    "gmail": Endpoint("imap.gmail.com", 993, "ssl"),
    "proton_bridge": Endpoint("127.0.0.1", 1143, "starttls"),
}


def _resolve(acc: MailAccount, presets: dict[str, Endpoint], host: str | None, port: int | None,
             tls: Tls | None, kind: str) -> Endpoint:
    base = presets.get(acc.preset)
    h = host or (base.host if base else None)
    t: Tls = tls or (base.tls if base else "ssl")
    default_port = {"ssl": 465 if kind == "smtp" else 993, "starttls": 587 if kind == "smtp" else 143,
                    "none": 25 if kind == "smtp" else 143}[t]
    p = port or (base.port if base else default_port)
    if not h:
        raise MailSendError(f"no {kind.upper()} host configured for {acc.address}")
    if acc.preset == "proton_bridge" and h not in LOCAL_HOSTS:
        raise MailSendError("Proton Mail Bridge runs locally; its host must be 127.0.0.1 or localhost")
    return Endpoint(h, p, t)


def smtp_endpoint(acc: MailAccount) -> Endpoint:
    return _resolve(acc, _SMTP_PRESETS, acc.smtp_host, acc.smtp_port, acc.smtp_tls, "smtp")


def imap_endpoint(acc: MailAccount) -> Endpoint:
    return _resolve(acc, _IMAP_PRESETS, acc.imap_host, acc.imap_port, acc.imap_tls, "imap")


def saves_sent_copy_itself(acc: MailAccount) -> bool:
    """Gmail and Proton Bridge put SMTP-sent mail into Sent; custom servers usually don't."""
    return acc.preset in ("gmail", "proton_bridge")


# --- TLS --------------------------------------------------------------------------------


class PinnedContext(ssl.SSLContext):
    """Accepts exactly one certificate, identified by its SHA-256 fingerprint.

    Used only for Proton Bridge on localhost, whose certificate is self-signed.
    Verification is not disabled: a different certificate aborts the connection.
    """

    fingerprint: str = ""

    def wrap_socket(self, *args, **kwargs):  # type: ignore[override]
        sock = super().wrap_socket(*args, **kwargs)
        der = sock.getpeercert(binary_form=True) or b""
        if hashlib.sha256(der).hexdigest() != self.fingerprint:
            sock.close()
            raise ssl.SSLError("the server certificate does not match the pinned Proton Bridge certificate")
        return sock


def normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").replace(" ", "").strip().lower()


def pinned_context(fingerprint_sha256: str) -> PinnedContext:
    ctx = PinnedContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # replaced by the exact fingerprint check above
    ctx.fingerprint = normalize_fingerprint(fingerprint_sha256)
    return ctx


def ssl_context_for(acc: MailAccount) -> ssl.SSLContext:
    if acc.preset == "proton_bridge":
        if not acc.bridge_cert_sha256:
            raise MailSendError(
                "the Proton Bridge certificate fingerprint is not recorded; run `autoapp setup --section email`"
            )
        return pinned_context(acc.bridge_cert_sha256)
    return ssl.create_default_context()


def fetch_bridge_fingerprint(host: str = "127.0.0.1", port: int = 1143, timeout: float = 10) -> str:
    """Read the SHA-256 fingerprint of the local Bridge certificate (for the user to confirm in setup)."""
    if host not in LOCAL_HOSTS:
        raise MailSendError("fingerprints are only fetched from a local Proton Bridge")
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    conn = imaplib.IMAP4(host, port, timeout=timeout)
    try:
        conn.starttls(ssl_context=unverified)
        der = conn.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
        return hashlib.sha256(der).hexdigest()
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 - best effort
            pass


# --- sending ------------------------------------------------------------------------------


class Mailer(Protocol):
    def send(self, msg: EmailMessage, *, to_addrs: list[str] | None = None, keep_copy: bool = True) -> None: ...

    def check_login(self) -> None: ...


class SmtpMailer:
    """Sends through the account's SMTP server and, for ``custom``, appends to Sent."""

    def __init__(self, account: MailAccount, password: str, *, timeout: float = 30,
                 ssl_context: ssl.SSLContext | None = None) -> None:
        self.account = account
        self.password = password
        self.timeout = timeout
        self._ctx = ssl_context

    @property
    def username(self) -> str:
        return self.account.username or self.account.address

    def _ctx_for(self) -> ssl.SSLContext:
        return self._ctx or ssl_context_for(self.account)

    def _smtp(self) -> smtplib.SMTP:
        ep = smtp_endpoint(self.account)
        try:
            if ep.tls == "ssl":
                s: smtplib.SMTP = smtplib.SMTP_SSL(ep.host, ep.port, timeout=self.timeout, context=self._ctx_for())
            else:
                s = smtplib.SMTP(ep.host, ep.port, timeout=self.timeout)
                if ep.tls == "starttls":
                    s.starttls(context=self._ctx_for())
            s.login(self.username, self.password)
            return s
        except smtplib.SMTPAuthenticationError as err:
            raise MailAuthError(f"the SMTP server rejected the login for {self.username}") from err
        except (smtplib.SMTPException, OSError, ssl.SSLError) as err:
            raise MailSendError(f"could not connect to {ep.host}:{ep.port}: {err}") from err

    def check_login(self) -> None:
        s = self._smtp()
        try:
            s.quit()
        except smtplib.SMTPException:
            pass

    def send(self, msg: EmailMessage, *, to_addrs: list[str] | None = None, keep_copy: bool = True) -> None:
        """``to_addrs`` overrides the SMTP envelope only (the headers stay as they are);
        ``keep_copy=False`` skips the Sent-folder append (used by test mails)."""
        s = self._smtp()
        try:
            s.send_message(msg, to_addrs=to_addrs)
        except smtplib.SMTPException as err:
            raise MailSendError(f"sending failed: {err}") from err
        finally:
            try:
                s.quit()
            except smtplib.SMTPException:
                pass
        if keep_copy and not saves_sent_copy_itself(self.account):
            self.append_sent(msg)

    def append_sent(self, msg: EmailMessage) -> None:
        ep = imap_endpoint(self.account)
        folder = self.account.sent_folder or "Sent"
        try:
            if ep.tls == "ssl":
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(ep.host, ep.port, ssl_context=self._ctx_for(),
                                                        timeout=self.timeout)
            else:
                conn = imaplib.IMAP4(ep.host, ep.port, timeout=self.timeout)
                if ep.tls == "starttls":
                    conn.starttls(ssl_context=self._ctx_for())
            conn.login(self.username, self.password)
            try:
                quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
                conn.append(quoted, r"(\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            finally:
                conn.logout()
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as err:
            # The mail was sent; a missing Sent copy must not cause a resend.
            raise MailSentNoCopyError(f"sent, but could not store a copy in '{folder}': {err}") from err


class FakeMailer:
    """Records messages; can be told to fail (tests, dry runs)."""

    def __init__(self, fail_with: Exception | None = None, *, appends: bool = False) -> None:
        self.sent: list[EmailMessage] = []
        self.envelopes: list[list[str] | None] = []  # the SMTP recipients of each send
        self.appended: list[EmailMessage] = []
        self.fail_with = fail_with
        self.appends = appends
        self.login_checks = 0

    def check_login(self) -> None:
        self.login_checks += 1
        if isinstance(self.fail_with, MailAuthError):
            raise self.fail_with

    def send(self, msg: EmailMessage, *, to_addrs: list[str] | None = None, keep_copy: bool = True) -> None:
        if self.fail_with:
            raise self.fail_with
        self.sent.append(msg)
        self.envelopes.append(to_addrs)
        if self.appends:
            self.appended.append(msg)
