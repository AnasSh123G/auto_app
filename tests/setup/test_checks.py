"""RealChecks with injected fakes: no network, no mailbox, no real LLM."""

from __future__ import annotations

import imaplib
from types import SimpleNamespace

import pytest

from autoapp.config.models import MailAccount
from autoapp.llm.base import LLMAuthError, LLMTransientError
from autoapp.llm.fake import FakeLLM
from autoapp.llm.settings import LLMSettings
from autoapp.mail.connection import FakeMailer, MailAuthError, MailSendError
from autoapp.setup.checks import FakeChecks, RealChecks

ACCOUNT = MailAccount(preset="gmail", address="me@gmail.com")


class FakeImap:
    def __init__(self, select_status: str = "OK") -> None:
        self.select_status = select_status
        self.selected: list[tuple[str, bool]] = []
        self.logged_out = False

    def select(self, folder: str, readonly: bool = False):
        self.selected.append((folder, readonly))
        return self.select_status, [b"1"]

    def logout(self):
        self.logged_out = True


def test_llm_check_makes_one_minimal_request():
    fake = FakeLLM({"evaluate": ["OK"]})
    seen = {}

    def factory(settings, task, env):
        seen["args"] = (settings.resolve(task)[0], task, dict(env))
        return fake

    result = RealChecks(client_factory=factory).llm(LLMSettings(), "evaluate", {"ANTHROPIC_API_KEY": "k"})
    assert result.ok
    assert len(fake.calls) == 1 and seen["args"][:2] == ("anthropic", "evaluate")


def test_llm_check_reports_rejected_key_without_leaking_it():
    def factory(settings, task, env):
        raise LLMAuthError("401 invalid x-api-key sk-ant-SECRET")

    result = RealChecks(client_factory=factory).llm(LLMSettings(), "evaluate", {})
    assert not result.ok and "rejected the API key" in result.message
    assert "SECRET" not in result.message


def test_llm_check_reports_other_failures():
    fake = FakeLLM({"write": [LLMTransientError("overloaded")]})
    result = RealChecks(client_factory=lambda *_: fake).llm(LLMSettings(), "write", {})
    assert not result.ok and "overloaded" in result.message


def test_llm_check_with_real_factory_reports_missing_key():
    result = RealChecks().llm(LLMSettings(), "evaluate", {})
    assert not result.ok and "ANTHROPIC_API_KEY" in result.message


def test_imap_check_success_opens_folder_read_only():
    conn = FakeImap()
    result = RealChecks(imap_connect=lambda acc, pw: conn).imap(ACCOUNT, "pw", "Jobalerts")
    assert result.ok
    assert conn.selected == [('"Jobalerts"', True)] and conn.logged_out


def test_imap_check_authentication_failure():
    def connect(acc, pw):
        raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    result = RealChecks(imap_connect=connect).imap(ACCOUNT, "wrong", "Jobalerts")
    assert not result.ok and result.message.startswith("IMAP login failed")
    assert "wrong" not in result.message


def test_imap_check_missing_folder_and_unreachable_server():
    result = RealChecks(imap_connect=lambda a, p: FakeImap("NO")).imap(ACCOUNT, "pw", "Nope")
    assert not result.ok and "'Nope' does not exist" in result.message

    def offline(acc, pw):
        raise OSError("Name or service not known")

    result = RealChecks(imap_connect=offline).imap(ACCOUNT, "pw", "Jobalerts")
    assert not result.ok and "cannot reach" in result.message


def test_smtp_check_logs_in_without_sending():
    mailer = FakeMailer()
    result = RealChecks(mailer_factory=lambda acc, pw: mailer).smtp(ACCOUNT, "pw")
    assert result.ok and mailer.login_checks == 1 and mailer.sent == []


def test_smtp_check_failures():
    bad = FakeMailer(fail_with=MailAuthError("the SMTP server rejected the login for me@gmail.com"))
    result = RealChecks(mailer_factory=lambda acc, pw: bad).smtp(ACCOUNT, "pw")
    assert not result.ok and "SMTP login failed" in result.message

    class Broken:
        def check_login(self):
            raise MailSendError("could not connect to smtp.gmail.com:465")

    result = RealChecks(mailer_factory=lambda acc, pw: Broken()).smtp(ACCOUNT, "pw")
    assert not result.ok and "could not connect" in result.message


def test_arbeitsagentur_check():
    calls = []

    def ok(url, **kwargs):
        calls.append((url, kwargs["params"]))
        return SimpleNamespace(raise_for_status=lambda: None)

    assert RealChecks(http_get=ok).arbeitsagentur().ok
    assert calls[0][0].endswith("/pc/v6/jobs") and calls[0][1]["size"] == 1

    def down(url, **kwargs):
        raise OSError("network unreachable")

    result = RealChecks(http_get=down).arbeitsagentur()
    assert not result.ok and "not reachable" in result.message


def test_bridge_fingerprint_is_delegated():
    checks = RealChecks(fingerprint_fetcher=lambda host, port: f"{host}:{port}")
    assert checks.bridge_fingerprint("127.0.0.1", 1143) == "127.0.0.1:1143"


def test_fake_checks_sequence_and_calls():
    from autoapp.setup.checks import CheckResult

    fake = FakeChecks(imap=[CheckResult(False, "x"), CheckResult(True, "ok")])
    assert not fake.imap(ACCOUNT, "p", "f").ok
    assert fake.imap(ACCOUNT, "p", "f").ok and fake.imap(ACCOUNT, "p", "f").ok
    assert fake.names() == ["imap", "imap", "imap"]
    with pytest.raises(OSError):
        FakeChecks(fingerprint=OSError("x")).bridge_fingerprint("h", 1)
