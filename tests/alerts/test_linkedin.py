"""LinkedIn parser and parser registry."""

from __future__ import annotations

from email.message import EmailMessage

from autoapp.sources.alerts import REGISTRY, parse_email, parser_for
from autoapp.sources.alerts.linkedin import LinkedInParser
from tests.alerts.conftest import load_eml

IDS = ["4476569290", "4476973777", "4475475300", "4440002570", "4440012224", "4475402162"]


def test_six_cards_in_order(linkedin_msg: EmailMessage) -> None:
    cards = LinkedInParser().parse(linkedin_msg)
    assert [c.source_job_id for c in cards] == IDS
    assert all(c.source == "linkedin-alert" and c.section == "match" for c in cards)
    assert cards[0].url == f"https://www.linkedin.com/jobs/view/{IDS[0]}/"


def test_card_fields(linkedin_msg: EmailMessage) -> None:
    cards = LinkedInParser().parse(linkedin_msg)
    first = cards[0]
    assert first.title == "Data Scientist / Softwareentwickler (m/w/d)"
    assert first.company == "AITAD GmbH"
    assert first.locations == ["Offenburg"]
    assert first.badges == ["Apply with resume & profile"]
    assert cards[2].badges == ["2 school alumni"]
    assert cards[2].locations == ["Berlin, Germany"]
    assert cards[3].badges == ["This company is actively hiring"]
    # Same role in two cities stays two cards (grouping happens at dedup time).
    assert (cards[3].company, cards[3].locations) == ("4flow", ["Düsseldorf"])
    assert (cards[4].company, cards[4].locations) == ("4flow", ["Rüsselsheim"])
    assert cards[5].badges == ["7 school alumni"]


def test_footer_is_not_a_card(linkedin_msg: EmailMessage) -> None:
    titles = " ".join(c.title for c in LinkedInParser().parse(linkedin_msg))
    for text in ("See all jobs", "Manage your job alerts", "Unsubscribe", "Premium"):
        assert text not in titles


def test_registry_matches_from_only(linkedin_msg: EmailMessage) -> None:
    assert parser_for(linkedin_msg).name == "linkedin"  # type: ignore[union-attr]
    assert {p.name for p in REGISTRY} == {"linkedin", "stepstone", "indeed", "workwise"}
    # Recipient must never decide: an unknown sender writing to the same recipient is unknown.
    msg = EmailMessage()
    msg["From"] = "Jobs <someone@xing.com>"
    msg["To"] = linkedin_msg["To"]
    msg.set_content("hello")
    assert parser_for(msg) is None


def test_from_match_is_case_insensitive(linkedin_msg: EmailMessage) -> None:
    del linkedin_msg["From"]
    linkedin_msg["From"] = "JobAlerts-NoReply@LinkedIn.com"
    assert parse_email(linkedin_msg).parser_name == "linkedin"


def test_unknown_sender_is_unrecognised() -> None:
    msg = EmailMessage()
    msg["From"] = "someone@xing.com"
    msg["Subject"] = "New jobs"
    msg.set_content("Data Scientist\nACME\nBerlin\n")
    result = parse_email(msg)
    assert result.parser_name is None and result.cards == [] and result.warnings == []


def test_zero_card_fixture_warns() -> None:
    result = parse_email(load_eml("linkedin_zero.eml"))
    assert result.parser_name == "linkedin"
    assert result.cards == []
    assert any("parser may be outdated" in w for w in result.warnings)


def test_real_fixture_has_no_warning(linkedin_msg: EmailMessage) -> None:
    assert parse_email(linkedin_msg).warnings == []


def test_singular_alum_badge_is_not_a_field() -> None:
    block = ("Softwareentwickler Java (m/w/d)\r\nBayerische Versorgungskammer\r\n"
             "Greater Munich Metropolitan Area\r\n\r\n1 school alum\r\n"
             "View job: https://www.linkedin.com/comm/jobs/view/4475748891/?trackingId=x\r\n")
    card = LinkedInParser._parse_block(block)
    assert card is not None
    assert (card.title, card.company, card.locations) == (
        "Softwareentwickler Java (m/w/d)", "Bayerische Versorgungskammer", ["Greater Munich Metropolitan Area"])
    assert card.badges == ["1 school alum"]
