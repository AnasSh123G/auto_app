"""IMAP side of drafts (add-menu-and-run-modes 2.2): folder discovery, APPEND, duplicates, Sent search."""

from __future__ import annotations

import email
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from autoapp.config.models import MailAccount
from autoapp.mail.drafts import (
    DraftFolderNotFound,
    ImapDraftStore,
    find_special_folder,
    imap_utf7_decode,
    imap_utf7_encode,
    parse_list_line,
)

GMAIL_DE = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
    b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Entw&APw-rfe"',
    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Gesendet"',
    b'(\\HasNoChildren) "/" "Jobalerts"',
]
PLAIN = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Drafts"', b'(\\HasNoChildren) "/" "Sent"']


class FakeImap:
    """Enough of imaplib.IMAP4 for the draft store."""

    def __init__(self, lines, *, drop_after_append=False):
        self.lines = lines
        self.boxes: dict[str, list[tuple[str, bytes]]] = {}
        self.selected = None
        self.drop_after_append = drop_after_append
        self.appends = 0

    def list(self):
        return "OK", list(self.lines)

    def select(self, name, readonly=False):
        self.selected = name.strip('"')
        return "OK", [b"1"]

    def append(self, mailbox, flags, date_time, message):
        self.appends += 1
        self.boxes.setdefault(mailbox.strip('"'), []).append((flags, message))
        if self.drop_after_append:
            raise OSError("connection reset")
        return "OK", [b"APPEND completed"]

    def search(self, charset, *criteria):
        msgs = self.boxes.get(self.selected, [])
        crit = list(criteria)
        hits = []
        for i, (_, raw) in enumerate(msgs, 1):
            m = email.message_from_bytes(raw, policy=policy.default)
            if crit[0] == "HEADER" and crit[1] == "Message-ID":
                if str(m["Message-ID"]) == crit[2].strip('"'):
                    hits.append(i)
            elif crit[0] == "SINCE" and crit[2] == "TO" and crit[3].strip('"') in str(m["To"]):
                hits.append(i)
        return "OK", [" ".join(str(h) for h in hits).encode()]

    def fetch(self, num, spec):
        raw = self.boxes[self.selected][int(num) - 1][1]
        head = raw.split(b"\r\n\r\n", 1)[0] if b"\r\n\r\n" in raw else raw.split(b"\n\n", 1)[0]
        return "OK", [(b"1 (BODY[HEADER.FIELDS (SUBJECT DATE TO)] {n}", head + b"\r\n\r\n"), b")"]

    def logout(self):
        return "BYE", []


ACCOUNT = MailAccount(preset="gmail", address="me@gmail.com")


def msg(to="hr@firma.de", subject="Bewerbung als Entwickler", mid="<m1@gmail.com>", when=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "me@gmail.com", to, subject, mid
    m["Date"] = format_datetime(when or datetime.now(UTC))
    m.set_content("Hallo")
    return m


def test_utf7_round_trip():
    assert imap_utf7_encode("[Gmail]/Entwürfe") == "[Gmail]/Entw&APw-rfe"
    assert imap_utf7_decode("[Gmail]/Entw&APw-rfe") == "[Gmail]/Entwürfe"
    assert imap_utf7_decode(imap_utf7_encode("A & B")) == "A & B"


def test_parse_list_line():
    assert parse_list_line(GMAIL_DE[2]) == ({"\\hasnochildren", "\\drafts"}, "[Gmail]/Entw&APw-rfe")


def test_localised_gmail_drafts_folder_found_by_attribute():
    store = ImapDraftStore(ACCOUNT, "pw", imap=FakeImap(GMAIL_DE))
    assert store.drafts_folder() == "[Gmail]/Entw&APw-rfe"
    assert store.sent_folder() == "[Gmail]/Gesendet"


def test_fallback_names_without_attributes():
    assert ImapDraftStore(ACCOUNT, "pw", imap=FakeImap(PLAIN)).drafts_folder() == "Drafts"


def test_configured_folder_wins():
    acct = ACCOUNT.model_copy(update={"drafts_folder": "Jobalerts"})
    assert ImapDraftStore(acct, "pw", imap=FakeImap(GMAIL_DE)).drafts_folder() == "Jobalerts"
    assert find_special_folder(GMAIL_DE, "\\Drafts", "[Gmail]/Entwürfe", ()) == "[Gmail]/Entw&APw-rfe"


def test_no_folder_found_names_the_setting():
    store = ImapDraftStore(ACCOUNT, "pw", imap=FakeImap([b'(\\HasNoChildren) "/" "INBOX"']))
    with pytest.raises(DraftFolderNotFound, match="mail.sending.drafts_folder"):
        store.drafts_folder()


def test_append_sets_the_draft_flag_and_keeps_the_message():
    imap = FakeImap(GMAIL_DE)
    store = ImapDraftStore(ACCOUNT, "pw", imap=imap)
    folder = store.drafts_folder()
    store.append_draft(folder, msg())
    flags, raw = imap.boxes[folder][0]
    assert "\\Draft" in flags
    assert email.message_from_bytes(raw, policy=policy.default)["To"] == "hr@firma.de"
    assert store.has_message(folder, "<m1@gmail.com>") and not store.has_message(folder, "<other@x>")


def test_lost_confirmation_is_found_again_by_message_id():
    imap = FakeImap(GMAIL_DE, drop_after_append=True)
    store = ImapDraftStore(ACCOUNT, "pw", imap=imap)
    folder = store.drafts_folder()
    with pytest.raises(OSError):
        store.append_draft(folder, msg())
    assert store.has_message(folder, "<m1@gmail.com>")  # so the next attempt records it instead of appending


def test_find_sent_matches_recipient_subject_and_time():
    imap = FakeImap(GMAIL_DE)
    store = ImapDraftStore(ACCOUNT, "pw", imap=imap)
    saved_at = datetime.now(UTC) - timedelta(hours=1)
    imap.boxes["[Gmail]/Gesendet"] = [("()", msg(subject="Bewerbung als Entwickler", mid="<new@gmail.com>").as_bytes())]
    assert store.find_sent("hr@firma.de", "Bewerbung als Entwickler", saved_at)
    assert not store.find_sent("hr@firma.de", "Bewerbung als Tester", saved_at)  # edited subject: no match
    old = msg(when=saved_at - timedelta(days=2))
    imap.boxes["[Gmail]/Gesendet"] = [("()", old.as_bytes())]
    assert not store.find_sent("hr@firma.de", "Bewerbung als Entwickler", saved_at)  # sent before the draft
