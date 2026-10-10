"""StepStone parser."""

from __future__ import annotations

from email.message import EmailMessage

from autoapp.sources.alerts import parse_email
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.alerts.stepstone import StepStoneParser
from tests.alerts.conftest import load_eml


def _cards(msg: EmailMessage) -> tuple[list[AlertCard], list[AlertCard]]:
    cards = StepStoneParser().parse(msg)
    return (
        [c for c in cards if c.section == "match"],
        [c for c in cards if c.section == "recommended"],
    )


def test_counts_and_sources(stepstone_msg: EmailMessage) -> None:
    match, rec = _cards(stepstone_msg)
    assert len(match) == 8 and len(rec) == 4
    assert {c.source for c in match} == {"stepstone-alert"}
    assert {c.source for c in rec} == {"stepstone-alert-recommended"}


def test_first_card(stepstone_msg: EmailMessage) -> None:
    first = _cards(stepstone_msg)[0][0]
    assert first.company == "inovex GmbH"
    assert first.title == "Senior Python Backend Engineer* (AI Focus)"
    assert first.title_truncated is False
    assert first.locations == ["Karlsruhe", "Köln", "München", "Stuttgart"]
    assert first.locations_truncated is True
    assert first.company_size == "251-1000 Mitarbeiter"
    assert first.contract_type == "Feste Anstellung"
    assert (first.work_mode, first.working_time) == ("hybrid", "full_time")
    assert (first.salary_estimate_min, first.salary_estimate_max) == (75000, 90000)
    assert first.url and first.url.startswith("https://click.stepstone.de/f/a/DUMMY1")
    assert first.source_job_id is None
    assert "Passt hervorragend" in first.badges


def test_neu_marker_belongs_to_preceding_card(stepstone_msg: EmailMessage) -> None:
    match = _cards(stepstone_msg)[0]
    assert "Neu" in match[1].badges and match[1].company == "Creditreform"
    assert all("Neu" not in c.badges for c in match if c is not match[1])


def test_truncated_title_and_study_contract(stepstone_msg: EmailMessage) -> None:
    card = next(c for c in _cards(stepstone_msg)[0] if c.title.startswith("Duales Studium"))
    assert card.title_truncated is True
    assert not card.title.endswith(".")
    assert card.contract_type == "Ausbildung, Studium"
    assert card.company == "Seeberger GmbH"
    assert card.salary_estimate_min is None and card.work_mode is None


def test_freelance_contract_and_missing_size(stepstone_msg: EmailMessage) -> None:
    card = next(c for c in _cards(stepstone_msg)[0] if c.company.startswith("Rebuy"))
    assert card.contract_type == "Freie Mitarbeit/Projektmitarbeit"
    assert card.company_size is None
    assert card.title_truncated is True


def test_german_thousands_and_truncated_working_time(stepstone_msg: EmailMessage) -> None:
    card = next(c for c in _cards(stepstone_msg)[0] if c.company.startswith("Hamburger"))
    assert (card.salary_estimate_min, card.salary_estimate_max) == (70100, 81200)
    assert card.working_time == "any"  # "Teilzeit, Vo..." means part time and full time
    assert card.work_mode == "hybrid"


def test_recommended_section(stepstone_msg: EmailMessage) -> None:
    rec = _cards(stepstone_msg)[1]
    bavaria = rec[0]
    assert bavaria.company == "Bavaria Systems GmbH"
    assert (bavaria.salary_estimate_min, bavaria.salary_estimate_max) == (54000, 66000)  # monthly x 12
    assert rec[-1].company == "Sopra Steria"
    assert rec[-1].locations == ["bundesweit", "Berlin", "Frankfurt"]  # "Hambur..." dropped
    assert rec[-1].locations_truncated is True
    assert all(c.section == "recommended" for c in rec)


def test_footer_links_are_not_cards(stepstone_msg: EmailMessage) -> None:
    cards = StepStoneParser().parse(stepstone_msg)
    assert len(cards) == 12
    blob = " ".join(f"{c.title} {c.company}" for c in cards).lower()
    for text in ("weitere passende", "einstellungen", "abbestellen", "datenschutz", "facebook"):
        assert text not in blob


def test_handles_any_jobagent_address(stepstone_msg: EmailMessage) -> None:
    parser = StepStoneParser()
    assert parser.handles("noreply@jobagent.stepstone.de")
    assert parser.handles("Info@JobAgent.Stepstone.de".lower())
    assert not parser.handles("info@stepstone.de")
    assert not parser.handles("info@jobagent.stepstone.de.evil.com")


def test_zero_card_fixture_warns() -> None:
    result = parse_email(load_eml("stepstone_zero.eml"))
    assert result.parser_name == "stepstone" and result.cards == []
    assert any("parser may be outdated" in w for w in result.warnings)
