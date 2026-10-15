"""IMAP reader (read-only guarantees) and the ingestion loop."""

from __future__ import annotations

import imaplib
from email.message import EmailMessage
from typing import Any

import pytest

from autoapp.config.models import MailAccount
from autoapp.sources.alerts import FakeMailbox, ImapAlertMailbox, UnparsedEmail, ingest_alerts
from tests.alerts.conftest import FIXTURES, load_eml

FORBIDDEN = {"SELECT", "STORE", "EXPUNGE", "COPY", "MOVE", "UID", "APPEND", "DELETE", "CREATE", "RENAME"}


class RecordingIMAP(imaplib.IMAP4):
    """Real ``IMAP4`` command logic with the socket replaced by a recorder."""

    instances: list[RecordingIMAP] = []
    raw: dict[bytes, bytes] = {}

    def __init__(self, host: str = "", port: int = 0, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.commands: list[tuple[str, tuple[Any, ...]]] = []
        self.state = "AUTH"
        self.untagged_responses: dict[str, list[Any]] = {}
        self.is_readonly = False
        RecordingIMAP.instances.append(self)

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.commands.append(("LOGIN", (user,)))
        return "OK", [b"logged in"]

    def starttls(self, ssl_context: Any = None) -> tuple[str, list[bytes]]:
        self.commands.append(("STARTTLS", ()))
        return "OK", [b""]

    def logout(self) -> tuple[str, list[bytes]]:
        self.commands.append(("LOGOUT", ()))
        return "BYE", [b""]

    def _simple_command(self, name: str, *args: Any) -> tuple[str, list[Any]]:
        self.commands.append((name, args))
        return "OK", [b"done"]

    def _untagged_response(self, typ: str, dat: list[Any], name: str) -> tuple[str, list[Any]]:
        if name == "SEARCH":
            return "OK", [b" ".join(self.raw)]
        if name == "FETCH":
            num = self.commands[-1][1][0]
            data = self.raw[num.encode() if isinstance(num, str) else num]
            return "OK", [(b"%s (BODY[] {%d}" % (num.encode(), len(data)), data), b")"]
        return typ, dat


@pytest.fixture
def fake_imap(monkeypatch: pytest.MonkeyPatch) -> type[RecordingIMAP]:
    RecordingIMAP.instances = []
    RecordingIMAP.raw = {
        b"1": (FIXTURES / "linkedin_alert.eml").read_bytes(),
        b"2": (FIXTURES / "stepstone_alert.eml").read_bytes(),
    }
    monkeypatch.setattr(imaplib, "IMAP4_SSL", RecordingIMAP)
    monkeypatch.setattr(imaplib, "IMAP4", RecordingIMAP)
    return RecordingIMAP


def _gmail() -> MailAccount:
    return MailAccount(preset="gmail", address="max.mustermann@example.com")


def test_gmail_session_is_read_only(fake_imap: type[RecordingIMAP]) -> None:
    box = ImapAlertMailbox(_gmail(), "secret")
    messages = list(box.list_messages("Job alerts"))
    assert [m[0] for m in messages] == [
        "<dummy-linkedin-alert@example.com>",
        "<dummy-stepstone-alert@example.com>",
    ]
    conn = fake_imap.instances[0]
    assert (conn.host, conn.port) == ("imap.gmail.com", 993)
    names = [c[0] for c in conn.commands]
    assert names == ["LOGIN", "EXAMINE", "SEARCH", "FETCH", "FETCH", "LOGOUT"]
    assert not FORBIDDEN & set(names)
    assert conn.commands[1][1] == ('"Job alerts"',)  # quoted folder name
    assert conn.commands[2][1] == ("ALL",)
    for name, args in conn.commands:
        if name == "FETCH":
            assert args[1] == "(BODY.PEEK[])"


def test_since_days_uses_search_since(fake_imap: type[RecordingIMAP]) -> None:
    list(ImapAlertMailbox(_gmail(), "x", since_days=7).list_messages("Jobalerts"))
    search = next(c for c in fake_imap.instances[0].commands if c[0] == "SEARCH")
    assert search[1][0] == "SINCE" and len(search[1][1].split("-")) == 3


def test_proton_bridge_uses_starttls_with_context(fake_imap: type[RecordingIMAP]) -> None:
    account = MailAccount(preset="proton_bridge", address="max.mustermann@example.com")
    box = ImapAlertMailbox(account, "x")
    assert box.endpoint() == ("127.0.0.1", 1143, "starttls")
    list(box.list_messages("Jobalerts"))
    conn = fake_imap.instances[0]
    assert [c[0] for c in conn.commands][:3] == ["STARTTLS", "LOGIN", "EXAMINE"]


def test_custom_endpoint() -> None:
    account = MailAccount(
        preset="custom",
        address="max.mustermann@example.com",
        imap_host="mail.example.org",
        imap_port=1993,
        imap_tls="ssl",
    )
    assert ImapAlertMailbox(account, "x").endpoint() == ("mail.example.org", 1993, "ssl")
    plain = MailAccount(
        preset="custom", address="max.mustermann@example.com", imap_host="h", imap_tls="starttls"
    )
    assert ImapAlertMailbox(plain, "x").endpoint() == ("h", 143, "starttls")


class Store:
    """Stand-in for the ``processed_emails`` table."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, int]] = {}

    def is_processed(self, message_id: str) -> bool:
        return message_id in self.rows

    def mark(self, message_id: str, sender: str, cards: int) -> None:
        self.rows[message_id] = (sender, cards)


def _unknown() -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "alerts@example.com"
    msg["Message-ID"] = "<indeed-1@example.com>"
    msg.set_content("Data Scientist\nACME\nBerlin\n")
    return msg


def test_same_email_ingested_once_across_runs() -> None:
    mailbox = FakeMailbox(
        [("<a@x>", load_eml("linkedin_alert.eml")), ("<b@x>", load_eml("stepstone_alert.eml"))]
    )
    store = Store()
    first = ingest_alerts(mailbox, "Jobalerts", store.is_processed, store.mark)
    assert len(first.cards) == 18
    assert first.processed_ids == ["<a@x>", "<b@x>"]
    assert store.rows["<a@x>"] == ("jobalerts-noreply@linkedin.com", 6)
    assert store.rows["<b@x>"][1] == 12
    assert {mid for mid, _ in first.cards} == {"<a@x>", "<b@x>"}
    second = ingest_alerts(mailbox, "Jobalerts", store.is_processed, store.mark)
    assert second.cards == [] and second.processed_ids == []
    assert second.skipped_processed == 2


def test_unknown_sender_not_marked_processed() -> None:
    mailbox = FakeMailbox([("<i@x>", _unknown()), ("<a@x>", load_eml("linkedin_alert.eml"))])
    store = Store()
    result = ingest_alerts(mailbox, "Jobalerts", store.is_processed, store.mark)
    assert result.unrecognised == 1
    assert "<i@x>" not in store.rows and "<a@x>" in store.rows
    again = ingest_alerts(mailbox, "Jobalerts", store.is_processed, store.mark)
    assert again.unrecognised == 1  # still waiting for a parser


def test_zero_card_email_warns_and_is_not_marked() -> None:
    mailbox = FakeMailbox([("<z@x>", load_eml("stepstone_zero.eml"))])
    store = Store()
    result = ingest_alerts(mailbox, "Jobalerts", store.is_processed, store.mark)
    assert any("parser may be outdated" in w for w in result.warnings)
    assert "<z@x>" not in store.rows  # kept for the extraction fallback; see test_excerpts.py


def test_folder_is_passed_through_and_duplicates_collapse() -> None:
    msg = load_eml("linkedin_alert.eml")
    mailbox = FakeMailbox({"Jobalerts": [("<a@x>", msg), ("<a@x>", msg)], "Other": [("<o@x>", msg)]})
    store = Store()
    result = ingest_alerts(mailbox, "Jobalerts", store.is_processed, store.mark)
    assert len(result.cards) == 6


# --- account mail and per-sender sampling (quick test) -------------------------------------------------------------

_ACCOUNT_SENDERS = {
    "linkedin": "LinkedIn <jobalerts-noreply@linkedin.com>",
    "stepstone": "StepStone <noreply@jobagent.stepstone.de>",
    "indeed": "Indeed <alert@indeed.com>",
    "workwise": "Workwise <noreply@workwise.io>",
}


@pytest.mark.parametrize("name", sorted(_ACCOUNT_SENDERS))
def test_mail_without_a_job_link_is_not_a_job_alert_for_every_parser(name: str) -> None:
    from autoapp.sources.alerts.registry import parser_for
    from tests.alerts.conftest import account_mail

    msg = account_mail(_ACCOUNT_SENDERS[name], "Confirm your email: https://www.example.com/confirm?x=1\n")
    parser = parser_for(msg)
    assert parser is not None and parser.name == name and not parser.is_job_alert(msg)
    empty = EmailMessage()
    empty["From"] = _ACCOUNT_SENDERS[name]
    empty.set_content("")
    assert parser.is_job_alert(empty)  # conservative: no readable body, so it is treated as an alert


@pytest.mark.parametrize("fixture", ["linkedin_alert.eml", "stepstone_alert.eml", "stepstone_zero.eml", "indeed_match.eml"])
def test_the_alert_fixtures_are_job_alerts(fixture: str) -> None:
    from autoapp.sources.alerts.registry import parser_for

    msg = load_eml(fixture)
    parser = parser_for(msg)
    assert parser is not None and parser.is_job_alert(msg)


def test_workwise_and_indeed_job_links_make_an_alert_also_when_wrapped_in_a_tracking_address() -> None:
    from autoapp.sources.alerts.registry import parser_for
    from tests.alerts.conftest import account_mail

    for sender, link in ((_ACCOUNT_SENDERS["workwise"], "https://t.example.com/c?u=https%3A%2F%2Fwww.workwise.io%2Fjob%2F123-dev"),
                          (_ACCOUNT_SENDERS["indeed"], "https://de.indeed.com/viewjob?jk=0123456789abcdef")):
        msg = account_mail(sender, f"New job: {link}\n")
        parser = parser_for(msg)
        assert parser is not None and parser.is_job_alert(msg)


def test_account_mail_is_marked_processed_without_a_fallback_or_a_warning() -> None:
    from tests.alerts.conftest import account_mail

    calls: list[UnparsedEmail] = []
    store = Store()
    mailbox = FakeMailbox([("<acc@x>", account_mail(_ACCOUNT_SENDERS["workwise"], "Confirm: https://www.workwise.io/x\n"))])
    result = ingest_alerts(mailbox, "f", store.is_processed, store.mark, fallback=lambda e: calls.append(e) or [])
    assert store.rows["<acc@x>"] == ("noreply@workwise.io", 0) and result.not_alerts == ["<acc@x>"]
    assert calls == [] and result.warnings == [] and result.unprocessed == [] and result.parsers == {}


def test_an_alert_looking_email_without_a_card_is_still_read_by_the_fallback_and_counts_as_a_parser_miss() -> None:
    from tests.alerts.conftest import unreadable_linkedin

    calls: list[UnparsedEmail] = []
    store = Store()
    result = ingest_alerts(FakeMailbox([("<u@x>", unreadable_linkedin())]), "f", store.is_processed, store.mark,
                           fallback=lambda e: calls.append(e) or [], per_sender=1)
    assert len(calls) == 1 and result.parsers == {"linkedin": 0} and result.not_alerts == []


def test_per_sender_sampling_reads_the_newest_alert_of_each_known_sender() -> None:
    from tests.alerts.conftest import account_mail

    mailbox = FakeMailbox([
        ("<s1>", load_eml("stepstone_alert.eml")), ("<l-old>", load_eml("linkedin_alert.eml")),
        ("<l-new>", load_eml("linkedin_alert.eml")), ("<x>", _unknown()),
        ("<w1>", account_mail(_ACCOUNT_SENDERS["workwise"], "Confirm your email\n", "<w1>")),
        ("<w2>", account_mail(_ACCOUNT_SENDERS["workwise"], "Set a password\n", "<w2>"))])
    store = Store()
    result = ingest_alerts(mailbox, "f", store.is_processed, store.mark, per_sender=1)
    assert set(store.rows) == {"<s1>", "<l-new>"} and set(result.parsers) == {"linkedin", "stepstone"}
    store.rows.clear()
    assert set(ingest_alerts(mailbox, "f", store.is_processed, store.mark, per_sender=1, scan=1).parsers) == set()


def test_imap_per_sender_scan_reads_headers_first_and_never_sets_a_flag(fake_imap: type[RecordingIMAP]) -> None:
    from tests.alerts.conftest import account_mail

    fake_imap.raw = {
        b"1": (FIXTURES / "linkedin_alert.eml").read_bytes(),
        b"2": (FIXTURES / "stepstone_alert.eml").read_bytes(),
        b"3": account_mail(_ACCOUNT_SENDERS["workwise"], "Confirm your email\n", "<w1@x>").as_bytes(),
        b"4": account_mail(_ACCOUNT_SENDERS["workwise"], "Set a password\n", "<w2@x>").as_bytes(),
    }
    box = ImapAlertMailbox(_gmail(), "secret")
    picked = box.list_per_sender("Job alerts", lambda mid: False, 1, 60)
    assert {m[0] for m in picked} == {"<dummy-linkedin-alert@example.com>", "<dummy-stepstone-alert@example.com>"}
    commands = fake_imap.instances[0].commands
    names = [c[0] for c in commands]
    assert not FORBIDDEN & set(names) and names[:3] == ["LOGIN", "EXAMINE", "SEARCH"]
    fetches = [c[1][1] for c in commands if c[0] == "FETCH"]
    assert all(f.startswith("(BODY.PEEK[") for f in fetches)  # PEEK only: no \Seen
    assert fetches[:4] == ["(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])"] * 4  # all headers before any body
    assert fetches.count("(BODY.PEEK[])") == 4  # linkedin, stepstone and the two Workwise account mails


def test_imap_per_sender_scan_skips_processed_mail_and_is_bounded(fake_imap: type[RecordingIMAP]) -> None:
    box = ImapAlertMailbox(_gmail(), "secret")
    done = {"<dummy-linkedin-alert@example.com>"}
    assert [m[0] for m in box.list_per_sender("f", done.__contains__, 1, 60)] == ["<dummy-stepstone-alert@example.com>"]
    box.list_per_sender("f", lambda mid: False, 1, 1)  # only the newest message's header is looked at
    fetches = [c[1][0] for c in fake_imap.instances[-1].commands if c[0] == "FETCH"]
    assert fetches[0] == "2" and len(fetches) == 2  # message 2 (newest): its header and its body
