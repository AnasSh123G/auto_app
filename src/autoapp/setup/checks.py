"""Connection checks run at the end of the ``llm``, ``sources`` and ``email`` sections.

``Checks`` is injectable: ``RealChecks`` talks to the network, ``FakeChecks`` replays
canned results for tests. A check never sends an email and never changes a mailbox.
"""

from __future__ import annotations

import imaplib
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from autoapp.config.models import MailAccount
from autoapp.llm.base import Block, LLMAuthError, LLMClient
from autoapp.llm.factory import build_client
from autoapp.llm.settings import LLMSettings
from autoapp.mail.connection import (
    MailAuthError,
    MailError,
    SmtpMailer,
    fetch_bridge_fingerprint,
    ssl_context_for,
)


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check; ``message`` explains a failure (never contains a secret)."""

    ok: bool
    message: str = ""


OK = CheckResult(True, "ok")


class Checks(Protocol):
    def llm(self, settings: LLMSettings, task: str, env: Mapping[str, str]) -> CheckResult: ...

    def imap(self, account: MailAccount, password: str, folder: str) -> CheckResult: ...

    def smtp(self, account: MailAccount, password: str) -> CheckResult: ...

    def arbeitsagentur(self) -> CheckResult: ...

    def bridge_fingerprint(self, host: str, port: int) -> str:
        """SHA-256 fingerprint of the local Bridge certificate; raises when Bridge is unreachable."""
        ...


class RealChecks:
    """Network checks. Every collaborator can be replaced for tests."""

    def __init__(
        self,
        *,
        client_factory: Callable[[LLMSettings, str, Mapping[str, str]], LLMClient] = build_client,
        imap_connect: Callable[[MailAccount, str], imaplib.IMAP4] | None = None,
        mailer_factory: Callable[[MailAccount, str], Any] | None = None,
        http_get: Callable[..., Any] | None = None,
        fingerprint_fetcher: Callable[[str, int], str] | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._imap_connect = imap_connect or _default_imap_connect
        self._mailer_factory = mailer_factory or (lambda acc, pw: SmtpMailer(acc, pw))
        self._http_get = http_get
        self._fingerprint = fingerprint_fetcher or (lambda host, port: fetch_bridge_fingerprint(host, port))

    def llm(self, settings: LLMSettings, task: str, env: Mapping[str, str]) -> CheckResult:
        """One minimal request with the model that serves ``task``."""
        try:
            client = self._client_factory(settings, task, env)
            client.text(
                task=task,
                system=[Block("Answer with the single word OK.")],
                user="ping",
                max_tokens=16,
            )
        except LLMAuthError:
            return CheckResult(False, "the provider rejected the API key")
        except Exception as exc:  # noqa: BLE001 - any failure is a failed check
            return CheckResult(False, _first_line(f"{type(exc).__name__}: {exc}"))
        return OK

    def imap(self, account: MailAccount, password: str, folder: str) -> CheckResult:
        """Log in and open ``folder`` read-only."""
        from autoapp.sources.alerts.imap_reader import _quote

        try:
            conn = self._imap_connect(account, password)
        except imaplib.IMAP4.error as exc:
            return CheckResult(False, f"IMAP login failed: {_first_line(str(exc))}")
        except (OSError, ssl.SSLError, MailError) as exc:
            return CheckResult(False, f"cannot reach the IMAP server: {_first_line(str(exc))}")
        try:
            status, _ = conn.select(_quote(folder), readonly=True)
        except imaplib.IMAP4.error as exc:
            return CheckResult(False, f"cannot open the folder '{folder}': {_first_line(str(exc))}")
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
        if status != "OK":
            return CheckResult(False, f"logged in, but the folder '{folder}' does not exist")
        return OK

    def smtp(self, account: MailAccount, password: str) -> CheckResult:
        """Log in to the SMTP server and quit; nothing is sent."""
        try:
            self._mailer_factory(account, password).check_login()
        except MailAuthError as exc:
            return CheckResult(False, f"SMTP login failed: {_first_line(str(exc))}")
        except (MailError, OSError) as exc:
            return CheckResult(False, _first_line(str(exc)))
        return OK

    def arbeitsagentur(self) -> CheckResult:
        """One tiny search against the public Arbeitsagentur API."""
        from autoapp.sources.arbeitsagentur import API_KEY, BASE_URL, SEARCH_PATH, USER_AGENT

        try:
            get = self._http_get
            if get is None:
                import httpx

                get = httpx.get
            resp = get(
                BASE_URL + SEARCH_PATH,
                params={"was": "Python", "size": 1},
                headers={"X-API-Key": API_KEY, "User-Agent": USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
            return CheckResult(False, f"Arbeitsagentur API not reachable: {_first_line(str(exc))}")
        return OK

    def bridge_fingerprint(self, host: str, port: int) -> str:
        return self._fingerprint(host, port)


def _default_imap_connect(account: MailAccount, password: str) -> imaplib.IMAP4:
    from autoapp.sources.alerts.imap_reader import ImapAlertMailbox

    ctx = ssl_context_for(account) if account.preset == "proton_bridge" else None
    return ImapAlertMailbox(account, password, ssl_context=ctx, timeout=15)._connect()  # noqa: SLF001


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


# ------------------------------------------------------------------------------- fakes


class FakeChecks:
    """Canned check results for tests. Each argument is a ``CheckResult`` or a list of them
    (consumed in order, the last one repeats). ``calls`` records ``(name, *details)``.
    """

    def __init__(
        self,
        *,
        llm: CheckResult | list[CheckResult] = OK,
        imap: CheckResult | list[CheckResult] = OK,
        smtp: CheckResult | list[CheckResult] = OK,
        arbeitsagentur: CheckResult | list[CheckResult] = OK,
        fingerprint: str | Exception = "ab" * 32,
    ) -> None:
        self._results: dict[str, list[CheckResult]] = {
            "llm": _as_list(llm),
            "imap": _as_list(imap),
            "smtp": _as_list(smtp),
            "arbeitsagentur": _as_list(arbeitsagentur),
        }
        self.fingerprint = fingerprint
        self.calls: list[tuple[Any, ...]] = []

    def _take(self, name: str) -> CheckResult:
        queue = self._results[name]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def llm(self, settings: LLMSettings, task: str, env: Mapping[str, str]) -> CheckResult:
        self.calls.append(("llm", task, settings.resolve(task)[:2]))
        return self._take("llm")

    def imap(self, account: MailAccount, password: str, folder: str) -> CheckResult:
        self.calls.append(("imap", account.address, folder, password))
        return self._take("imap")

    def smtp(self, account: MailAccount, password: str) -> CheckResult:
        self.calls.append(("smtp", account.address, password))
        return self._take("smtp")

    def arbeitsagentur(self) -> CheckResult:
        self.calls.append(("arbeitsagentur",))
        return self._take("arbeitsagentur")

    def bridge_fingerprint(self, host: str, port: int) -> str:
        self.calls.append(("bridge_fingerprint", host, port))
        if isinstance(self.fingerprint, Exception):
            raise self.fingerprint
        return self.fingerprint

    def names(self) -> list[str]:
        return [str(c[0]) for c in self.calls]


def _as_list(value: CheckResult | list[CheckResult]) -> list[CheckResult]:
    return list(value) if isinstance(value, list) else [value]
