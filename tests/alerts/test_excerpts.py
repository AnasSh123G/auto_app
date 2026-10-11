"""Redacted email excerpts per card, and no silent zero-card emails (employer-first spec 1a, 1b)."""

from __future__ import annotations

import json
from email.message import EmailMessage

from autoapp.sources.alerts import AlertCard, FakeMailbox, Redactor, UnparsedEmail, ingest_alerts
from autoapp.sources.alerts.excerpt import EXCERPT_CHARS
from autoapp.steps.context import RunContext
from autoapp.steps.search import run_search
from tests.alerts.conftest import load_eml
from tests.helpers import make_workspace

EMAIL, PHONE, NAME = "max.mustermann@example.com", "+49 30 1234567", "Max Mustermann"


def personal() -> Redactor:
    class P:
        first_name, last_name, email, phone, street = "Max", "Mustermann", EMAIL, PHONE, "Musterstraße 1"

    return Redactor.from_personal(P())


def personal_linkedin() -> EmailMessage:
    """A LinkedIn alert that carries the recipient's data, an unsubscribe link and tracked job links."""
    msg = EmailMessage()
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["To"] = EMAIL
    msg["Subject"] = "Max Mustermann: 2 new jobs"
    msg["Message-ID"] = "<li-1@example.com>"
    msg.set_content(
        f"Hi {NAME},\nYour job alert for Developer\n\n"
        "-----------------------------------------------\n\n"
        "Backend Developer (m/w/d)\nAcme GmbH\nBerlin\n"
        f"View job: https://www.linkedin.com/comm/jobs/view/111/?trackingId=abc&refId=x&midToken=zz&email={EMAIL}\n\n"
        "-----------------------------------------------\n\n"
        "Data Engineer (m/w/d)\nBeta AG\nHamburg\n"
        "View job: https://www.linkedin.com/comm/jobs/view/222/?trackingId=def\n\n"
        "-----------------------------------------------\n\n"
        f"This email was intended for {NAME} (Software Developer in Berlin). Call us {PHONE} or 030/1234567.\n"
        f"Unsubscribe: https://www.linkedin.com/e/v2?e=1&t=unsub&email={EMAIL}\n"
        "https://www.linkedin.com/comm/psettings/email?lipi=urn&midToken=secret\n"
    )
    return msg


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, int]] = {}

    def is_processed(self, mid: str) -> bool:
        return mid in self.rows

    def mark(self, mid: str, sender: str, cards: int) -> None:
        self.rows[mid] = (sender, cards)


def test_every_card_gets_its_own_block_and_the_message_id() -> None:
    store = Store()
    result = ingest_alerts(FakeMailbox([("<a@x>", load_eml("linkedin_alert.eml"))]), "f", store.is_processed, store.mark)
    assert len(result.cards) == 6
    excerpts = {card.title: card.email_excerpt for _, card in result.cards}
    first, second = result.cards[0][1], result.cards[1][1]
    assert first.message_id == "<a@x>" and first.title in first.email_excerpt and first.company in first.email_excerpt
    assert second.title not in first.email_excerpt  # the block of one card, not the whole email
    assert all(0 < len(e) <= EXCERPT_CHARS for e in excerpts.values())


def test_excerpts_keep_job_links_without_query_and_drop_tracking_and_unsubscribe_links() -> None:
    result = ingest_alerts(FakeMailbox([("<p@x>", personal_linkedin())]), "f", Store().is_processed, Store().mark,
                           redactor=personal())
    [(_, a), (_, b)] = result.cards
    assert "https://www.linkedin.com/comm/jobs/view/111/" in a.email_excerpt
    for card in (a, b):
        assert "?" not in card.email_excerpt and "trackingId" not in card.email_excerpt
    assert "Unsubscribe" not in b.email_excerpt  # the footer is not part of a card


def test_redactor_removes_name_address_phone_unsubscribe_and_query() -> None:
    text = (f"Hallo {NAME}, {EMAIL}, Tel {PHONE}, 030 / 123 45 67, 0049 30 1234567, Musterstraße 1, MUSTERMANN, Max\n"
            "https://click.stepstone.de/abc/def?x=1 https://x.example/unsubscribe?id=1 "
            "https://jobs.acme.de/job/5?utm=1#top, https://www.linkedin.com/jobs/view/9/?trackingId=q")
    out = personal().redact(text)
    for secret in (NAME, "Max", "Mustermann", EMAIL, "1234567", "123 45 67", "Musterstraße", "click.stepstone", "unsubscribe",
                   "utm", "trackingId", "?"):
        assert secret.lower() not in out.lower(), (secret, out)
    assert "https://jobs.acme.de/job/5" in out and "https://www.linkedin.com/jobs/view/9/" in out


def test_a_zero_card_email_is_warned_with_sender_and_subject_and_left_unprocessed() -> None:
    store = Store()
    mailbox = FakeMailbox([("<z@x>", load_eml("stepstone_zero.eml"))])
    result = ingest_alerts(mailbox, "f", store.is_processed, store.mark)
    assert "<z@x>" not in store.rows and result.processed_ids == []
    [warning] = result.warnings
    assert "stepstone" in warning and "@jobagent.stepstone.de" in warning and "subject:" in warning
    assert [u.message_id for u in result.unprocessed] == ["<z@x>"] and result.unprocessed[0].body
    assert ingest_alerts(mailbox, "f", store.is_processed, store.mark).unprocessed  # read again next run


def test_a_parser_exception_is_warned_and_leaves_the_email_unprocessed(monkeypatch) -> None:
    def boom(self, msg):
        raise ValueError("layout changed")

    monkeypatch.setattr("autoapp.sources.alerts.linkedin.LinkedInParser.parse", boom)
    store = Store()
    mailbox = FakeMailbox([("<l@x>", load_eml("linkedin_alert.eml")), ("<s@x>", load_eml("stepstone_alert.eml"))])
    result = ingest_alerts(mailbox, "f", store.is_processed, store.mark)
    assert "<l@x>" not in store.rows and "<s@x>" in store.rows  # the other email is unaffected
    assert any("parser failed (ValueError: layout changed)" in w and "subject:" in w for w in result.warnings)
    assert result.unprocessed[0].reason.startswith("parser failed")


def test_a_fallback_that_names_cards_makes_the_email_processed() -> None:
    seen: list[UnparsedEmail] = []

    def fallback(email: UnparsedEmail) -> list[AlertCard]:
        seen.append(email)
        return [AlertCard(source="linkedin-alert", title="Backend Developer", company="Acme", source_job_id="5")]

    store = Store()
    result = ingest_alerts(FakeMailbox([("<z@x>", load_eml("linkedin_zero.eml"))]), "f", store.is_processed, store.mark,
                           fallback=fallback)
    assert store.rows["<z@x>"][1] == 1 and result.llm_extracted == 1 and result.unprocessed == []
    [(mid, card)] = result.cards
    assert mid == "<z@x>" and card.message_id == "<z@x>" and card.email_excerpt
    assert seen[0].sender == "jobalerts-noreply@linkedin.com" and seen[0].reason == "no job cards found"


def test_a_fallback_that_finds_nothing_or_fails_still_warns_and_leaves_it_unprocessed() -> None:
    for fallback in (lambda email: [], lambda email: 1 / 0):
        store = Store()
        result = ingest_alerts(FakeMailbox([("<z@x>", load_eml("linkedin_zero.eml"))]), "f", store.is_processed,
                               store.mark, fallback=fallback)
        assert store.rows == {} and result.unprocessed and any("stays unprocessed" in w for w in result.warnings)


def test_no_stored_email_excerpt_contains_the_profiles_email_phone_or_name(tmp_path) -> None:
    """Privacy: whatever the alert email says about the recipient never reaches ``job_sources.raw``."""
    ctx = RunContext.open(make_workspace(tmp_path))
    assert ctx.profile.personal.email == EMAIL
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<p@x>", personal_linkedin())]))
    assert summary.alert_cards == 2
    rows = ctx.conn.execute("SELECT raw FROM job_sources WHERE source = 'linkedin-alert'").fetchall()
    assert len(rows) == 2
    for row in rows:
        raw = json.loads(row["raw"])
        excerpt = raw["email_excerpt"]
        assert raw["message_id"] == "<p@x>" and excerpt
        for secret in (EMAIL, PHONE, "1234567", "Mustermann", "Max", "trackingId", "?", "unsubscribe"):
            assert secret.lower() not in excerpt.lower(), (secret, excerpt)
    ctx.close()


def test_run_search_warns_about_a_zero_card_email_and_keeps_it_unprocessed(tmp_path) -> None:
    ctx = RunContext.open(make_workspace(tmp_path))
    events: list = []
    ctx.events.subscribe(events.append)
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<z@x>", load_eml("linkedin_zero.eml"))]))
    assert any("jobalerts-noreply@linkedin.com" in w and "subject:" in w for w in summary.warnings)
    assert any(e.kind == "warning" and "left unprocessed" in e.detail for e in events)
    assert ctx.conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == 0
    assert summary.alert_unprocessed == 1 and summary.alert_emails == 1
    ctx.close()
