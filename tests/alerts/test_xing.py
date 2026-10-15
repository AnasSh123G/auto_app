"""Xing job-alert extraction against an anonymised real multipart layout."""

from __future__ import annotations

import email
import json
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from autoapp.sources.alerts import FakeMailbox
from autoapp.sources.alerts.registry import parse_email, parser_for
from autoapp.sources.alerts.xing import XingParser

FIXTURE = Path(__file__).parent.parent / "fixtures" / "alerts" / "xing_alert.eml"


def _message() -> EmailMessage:
    return email.message_from_bytes(FIXTURE.read_bytes(), policy=policy.default)


def _plain(text: str, *, subject: str = "Neue Stellenangebote") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "XING Jobs <jobs@mail.xing.com>"
    msg["Subject"] = subject
    msg.set_content(text)
    return msg


@pytest.mark.parametrize("address", ["jobs@xing.com", "jobs@mail.xing.com", "JOBS@MAIL.XING.COM"])
def test_sender_domain_and_subdomains(address: str) -> None:
    assert XingParser().handles(address)


@pytest.mark.parametrize("address", ["jobs@evilxing.com", "jobs@xing.com.evil.test", "jobs@xing.de", "xing.com"])
def test_sender_domain_spoofing_rejected(address: str) -> None:
    assert not XingParser().handles(address)


def test_registry_recognises_xing_and_parses_real_layout() -> None:
    msg = _message()
    assert parser_for(msg).name == "xing"
    result = parse_email(msg)
    assert result.parser_name == "xing" and result.warnings == []
    cards = result.cards
    assert len(cards) == 10  # duplicate image/title links and multipart alternatives yield one card per job
    assert all(c.source == "xing-alert" and c.section == "match" for c in cards)
    assert [(c.title, c.company, c.locations) for c in cards[:2]] == [
        ("Junior Softwareentwickler C# - .NET / Messtechnik (m/w/d)", "Mitutoyo CTL Germany GmbH", ["Oberndorf am Neckar"]),
        ("(Junior) Software Developer (m/w/d)", "Jobriver IT Jobbörse", ["Frankfurt am Main"]),
    ]
    assert cards[7].title == "Junior BI & Data Solution Engineer (m/w/d)"
    assert (cards[-1].company, cards[-1].locations) == ("Jobriver IT Jobbörse", ["Berlin"])
    assert all(c.working_time == "full_time" and c.contract_type is None for c in cards)
    assert all(not c.company_unknown and not c.title_truncated for c in cards)
    assert all(c.url is None and c.source_job_id is None for c in cards)
    # The sample displays salaries without marking them as platform estimates.
    assert all(c.salary_estimate_min is None and c.salary_estimate_max is None for c in cards)
    assert "Zu den Ersten gehören" in cards[0].badges


@pytest.mark.parametrize("content_type", ["plain", "html"])
def test_each_mime_alternative_contains_all_jobs(content_type: str) -> None:
    fixture = _message()
    msg = EmailMessage()
    msg["From"] = fixture["From"]
    msg["Subject"] = fixture["Subject"]
    msg.set_content(fixture.get_body((content_type,)).get_content(), subtype=content_type)
    cards = XingParser().parse(msg)
    assert len(cards) == 10
    assert cards[0].company == "Mitutoyo CTL Germany GmbH"
    assert cards[7].title == "Junior BI & Data Solution Engineer (m/w/d)"
    assert cards[-1].locations == ["Berlin"]
    assert all(card.working_time == "full_time" for card in cards)


def test_job_alert_detection_rejects_account_and_footer_links() -> None:
    parser = XingParser()
    assert parser.is_job_alert(_message())
    assert not parser.is_job_alert(_plain(
        "Passwort zurücksetzen: https://www.xing.com/m/DUMMY_ACCOUNT\n"
        "Abbestellen: https://www.xing.com/m/DUMMY_UNSUBSCRIBE", subject="Dein XING Passwort"))
    assert not parser.is_job_alert(_plain("Dein Profil wurde besucht.", subject="Neuigkeiten aus Deinem Netzwerk"))


def test_profile_notification_table_is_not_a_job_card() -> None:
    msg = _plain("", subject="Neues in deinem Netzwerk")
    msg.add_alternative('<table><tr><td><a href="https://www.xing.com/m/DUMMY_ACCOUNT">'
                        'Dein Profil wurde angesehen</a></td></tr><tr><td>XING GmbH</td></tr>'
                        '<tr><td>Berlin</td></tr></table>', subtype="html")
    assert XingParser().parse(msg) == []
    assert not XingParser().is_job_alert(msg)


def test_plain_alternative_recovers_jobs_missing_from_partial_html() -> None:
    fixture = _message()
    msg = _plain(fixture.get_body(("plain",)).get_content())
    html = fixture.get_body(("html",)).get_content().replace(
        "(Junior) Software Developer (m/w/d)",
        "<span>(Junior) Software</span><span>Developer (m/w/d)</span>")
    msg.add_alternative(html, subtype="html")
    cards = XingParser().parse(msg)
    assert len(cards) == 10
    assert sum(card.title == "(Junior) Software Developer (m/w/d)" for card in cards) == 1


def test_direct_job_url_is_canonical_and_missing_company_is_not_guessed() -> None:
    msg = EmailMessage()
    msg["From"] = "jobs@xing.com"
    msg.set_content('<html><body><a href="https://www.xing.com/jobs/berlin-backend-developer-123456789?tracking=SECRET#mail">Backend Developer</a></body></html>', subtype="html")
    [card] = XingParser().parse(msg)
    assert card.title == "Backend Developer"
    assert card.company == "" and card.company_unknown
    assert card.locations == [] and card.working_time is None
    assert card.url == "https://www.xing.com/jobs/berlin-backend-developer-123456789"
    assert card.source_job_id == "123456789"


def test_malformed_and_spoofed_links_never_become_cards() -> None:
    msg = EmailMessage()
    msg["From"] = "jobs@xing.com"
    msg.set_content('''<html><body>
      <a href="https://xing.com.evil.test/jobs/berlin-developer-123456789">Developer at Evil</a>
      <a href="https://[invalid/jobs/developer-123456789">Malformed developer</a>
      <a href="javascript:alert(1)">Developer</a>
      <a href="https://www.xing.com/m/DUMMY_UNSUBSCRIBE">Abbestellen</a>
      </body></html>''', subtype="html")
    assert XingParser().parse(msg) == []


def test_repeated_direct_job_id_is_one_card_even_with_different_link_text() -> None:
    msg = EmailMessage()
    msg["From"] = "jobs@xing.com"
    msg.set_content('<a href="https://www.xing.com/jobs/berlin-developer-123">Backend Developer</a>'
                    '<a href="https://www.xing.com/jobs/berlin-developer-123?ref=mail">Details</a>',
                    subtype="html")
    cards = XingParser().parse(msg)
    assert len(cards) == 1 and cards[0].title == "Backend Developer"


def test_duplicate_plain_cards_deduplicate_without_merging_distinct_locations() -> None:
    block = "----------------------------------------\nZu den Ersten gehören\nBackend Developer\n=> https://www.xing.com/m/DUMMY_JOB\n\nExample GmbH\nBerlin\n\nVollzeit\n"
    cards = XingParser().parse(_plain(block + block + block.replace("Berlin", "Hamburg")))
    assert [(c.company, c.locations) for c in cards] == [("Example GmbH", ["Berlin"]), ("Example GmbH", ["Hamburg"])]


def test_email_first_search_ingests_all_cards_without_storing_tracking_tokens(tmp_path) -> None:
    from autoapp.steps.context import RunContext
    from autoapp.steps.search import run_search
    from tests.helpers import make_workspace

    ctx = RunContext.open(make_workspace(tmp_path, alert_mode="search_first"))
    try:
        summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<xing-fixture@example.test>", _message())]))
        assert summary.alert_cards == 10 and summary.alert_llm_extracted == 0
        rows = ctx.conn.execute("SELECT raw, url FROM job_sources WHERE source = 'xing-alert'").fetchall()
        assert len(rows) == 10
        for row in rows:
            assert row["url"] is None
            assert "DUMMY_" not in row["raw"] and "/m/" not in row["raw"]
            assert json.loads(row["raw"])["email_excerpt"]
    finally:
        ctx.close()


def test_every_card_excerpt_contains_its_own_job_only() -> None:
    from autoapp.sources.alerts.excerpt import Redactor, card_excerpts

    msg = _message()
    cards = XingParser().parse(msg)
    excerpts = card_excerpts(msg, cards, Redactor())
    for card, excerpt in zip(cards, excerpts, strict=True):
        assert card.title in excerpt
        assert card.company in excerpt
        assert not any(other.title in excerpt for other in cards if other != card)


def test_zero_card_fallback_keeps_xing_source(tmp_path) -> None:
    from autoapp.llm.fake import FakeLLM
    from autoapp.sources.alerts.imap_reader import UnparsedEmail
    from autoapp.sources.alerts.llm_extract import AlertExtractor
    from autoapp.steps.context import RunContext
    from tests.helpers import make_workspace

    ctx = RunContext.open(make_workspace(tmp_path, alert_mode="search_first"))
    ctx.clients["alert_extract"] = FakeLLM({"alert_extract": [{"jobs": [
        {"title": "Backend Developer", "company": "Example GmbH", "city": "Berlin", "job_link": ""},
    ]}]})
    try:
        [card] = AlertExtractor(ctx, max_calls=1).extract_email(UnparsedEmail(
            message_id="<unknown-layout@example.test>", sender="jobs@mail.xing.com", parser="xing",
            subject="Neue Stellenangebote", reason="no job cards found",
            body="Backend Developer\nExample GmbH\nBerlin\n"))
        assert card.source == "xing-alert" and card.llm_extracted
        assert (card.company, card.locations) == ("Example GmbH", ["Berlin"])
        assert card.url is None and card.source_job_id is None
    finally:
        ctx.close()


def test_per_sender_sampling_selects_latest_xing_alert_and_skips_account_mail() -> None:
    from autoapp.sources.alerts import ingest_alerts

    mailbox = FakeMailbox([
        ("<older@example.test>", _message()),
        ("<newer@example.test>", _message()),
        ("<account@example.test>", _plain("Passwort: https://www.xing.com/m/DUMMY_ACCOUNT", subject="Dein Passwort")),
    ])
    marked: list[str] = []
    result = ingest_alerts(mailbox, "INBOX", lambda mid: False,
                           lambda mid, sender, count: marked.append(mid), per_sender=1)
    assert result.parsers == {"xing": 10}
    assert len(result.cards) == 10
    assert {mid for mid, card in result.cards} == {"<newer@example.test>"}
    assert "<older@example.test>" not in marked
