from __future__ import annotations

from datetime import date, timedelta

import pytest

from autoapp.config.models import SearchProfile
from autoapp.discovery.prefilter import Candidate, check
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.models import RawJob

TODAY = date(2026, 10, 8)


def profile(**kw) -> SearchProfile:
    return SearchProfile(name="p", keywords=["python"], **kw)


def cand(**kw) -> Candidate:
    kw.setdefault("title", "Backend Entwickler (m/w/d)")
    kw.setdefault("company", "Acme GmbH")
    return Candidate(**kw)


def run(c: Candidate, p: SearchProfile) -> str | None:
    return check(c, p, today=TODAY)


def test_excluded_keyword_reason_uses_configured_spelling():
    p = profile(exclude_keywords=["Werkstudent"])
    c = Candidate.from_raw_job(RawJob(source="x", title="Werkstudent Softwareentwicklung", company="Acme"))
    assert run(c, p) == "excluded keyword: Werkstudent"


def test_keyword_is_case_umlaut_and_word_insensitive():
    p = profile(exclude_keywords=["Büro"])
    assert run(cand(title="BUERO Assistenz"), p) == "excluded keyword: Büro"
    p = profile(exclude_keywords=["Java"])
    assert run(cand(title="JavaScript Entwickler"), p) is None
    assert run(cand(title="Senior Java-Entwickler"), p) == "excluded keyword: Java"


def test_company_blocklist():
    p = profile(exclude_companies=["Example Consulting AG"])
    assert run(cand(company="Example Consulting"), p) == "excluded company: Example Consulting AG"
    assert run(cand(company="EXAMPLE CONSULTING AG Munich"), p) == "excluded company: Example Consulting AG"
    assert run(cand(company="Other Corp"), p) is None


def test_company_match_is_on_whole_words():
    p = profile(exclude_companies=["SAP"])
    assert run(cand(company="Sapient GmbH"), p) is None
    assert run(cand(company="SAP SE"), p) == "excluded company: SAP"


def test_unconfigured_profile_drops_nothing():
    c = cand(
        title="Werkstudent",
        posted_at=TODAY - timedelta(days=900),
        ad_salary_max=1,
        work_mode="onsite",
        working_time="part_time",
        contract_type="Freie Mitarbeit/Projektmitarbeit",
    )
    assert run(c, profile()) is None
    assert run(c, profile(work_mode="any", working_time="any", contract_type="any")) is None


def test_old_posting_filtered():
    p = profile(max_posting_age_days=14)
    assert run(cand(posted_at=TODAY - timedelta(days=30)), p) == "posted 30 days ago (limit 14)"
    assert run(cand(posted_at=TODAY - timedelta(days=14)), p) is None
    assert run(cand(posted_at=None), p) is None


def test_salary_maximum_below_minimum():
    p = profile(min_salary=60000)
    assert run(cand(ad_salary_max=48000), p) == "salary up to 48000 EUR is below the minimum of 60000"
    assert run(cand(ad_salary_max=60000), p) is None
    assert run(cand(ad_salary_max=None), p) is None


def test_no_salary_filter_never_drops_on_salary():
    assert run(cand(ad_salary_max=1000), profile()) is None


def test_estimate_does_not_filter():
    p = profile(min_salary=60000)
    job = RawJob(
        source="x", title="Dev", company="Acme", salary_estimate_min=48000, salary_estimate_max=55000
    )
    assert run(Candidate.from_raw_job(job), p) is None
    card = AlertCard(
        source="stepstone-alert",
        title="Dev",
        company="Acme",
        salary_estimate_min=48000,
        salary_estimate_max=55000,
    )
    assert run(Candidate.from_alert_card(card), p) is None


def test_stepstone_freelance_card_filtered_for_permanent():
    card = AlertCard(
        source="stepstone-alert",
        title="Dev",
        company="Acme",
        contract_type="Freie Mitarbeit/Projektmitarbeit",
    )
    reason = run(Candidate.from_alert_card(card), profile(contract_type="permanent"))
    assert reason == "contract type freelance does not match the wanted permanent"


def test_training_card_filtered_by_position_type():
    card = AlertCard(
        source="stepstone-alert", title="Dev", company="Acme", contract_type="Ausbildung, Studium"
    )
    p = profile(exclude_position_types=["training"])
    assert run(Candidate.from_alert_card(card), p) == "excluded position type: training"


@pytest.mark.parametrize(
    ("configured", "text"),
    [
        ("training", "Duales Studium"),
        ("training", "Trainee"),
        ("internship", "Praktikum"),
        ("internship", "Internship"),
        ("working_student", "Werkstudent"),
        ("freelance", "Freie Mitarbeit/Projektmitarbeit"),
        ("Praktikum", "Praktikum"),
        ("Werkstudent", "Werkstudententätigkeit"),
    ],
)
def test_position_type_aliases(configured, text):
    assert run(cand(contract_type=text), profile(exclude_position_types=[configured])) == (
        f"excluded position type: {configured}"
    )


def test_position_type_does_not_match_unrelated_text():
    p = profile(exclude_position_types=["training", "internship"])
    assert run(cand(contract_type="Feste Anstellung"), p) is None
    assert run(cand(contract_type=None), p) is None


def test_permanent_profile():
    p = profile(contract_type="permanent")
    assert run(cand(contract_type="Feste Anstellung"), p) is None
    assert run(cand(contract_type="permanent"), p) is None
    assert run(cand(contract_type="Unbefristet"), p) is None
    assert run(cand(contract_type="Befristet"), p) is not None
    assert run(cand(contract_type="fixed_term"), p) is not None
    assert run(cand(contract_type="Feste Anstellung, Befristet"), p) is None
    # Training is a position type, not a contract kind: only exclude_position_types drops it.
    assert run(cand(contract_type="Ausbildung, Studium"), p) is None


def test_fixed_term_profile():
    p = profile(contract_type="fixed_term")
    assert run(cand(contract_type="fixed_term"), p) is None
    assert run(cand(contract_type="Befristet"), p) is None
    assert run(cand(contract_type="Feste Anstellung"), p) is not None
    assert run(cand(contract_type="Freelance"), p) is not None
    assert run(cand(contract_type=None), p) is None


def test_work_mode_rules():
    remote, onsite, hybrid = (
        profile(work_mode="remote"),
        profile(work_mode="onsite"),
        profile(work_mode="hybrid"),
    )
    assert run(cand(work_mode="onsite"), remote) == "work mode onsite does not match the wanted remote"
    assert run(cand(work_mode="Vor Ort"), remote) is not None
    assert run(cand(work_mode="remote"), onsite) == "work mode remote does not match the wanted onsite"
    # hybrid listings are compatible with both, and a hybrid profile never drops
    assert run(cand(work_mode="hybrid"), remote) is None
    assert run(cand(work_mode="hybrid"), onsite) is None
    for mode in ("onsite", "remote", "hybrid", None):
        assert run(cand(work_mode=mode), hybrid) is None
    # unknown never drops
    assert run(cand(work_mode=None), remote) is None
    assert run(cand(work_mode="flexibel"), onsite) is None


def test_working_time():
    full = profile(working_time="full_time")
    assert (
        run(cand(working_time="part_time"), full)
        == "working time part_time does not match the wanted full_time"
    )
    assert run(cand(working_time="Teilzeit"), full) is not None
    assert run(cand(working_time="full_time"), full) is None
    assert run(cand(working_time="Vollzeit, Teilzeit"), full) is None
    assert run(cand(working_time="any"), full) is None
    assert run(cand(working_time=None), full) is None
    assert run(cand(working_time="full_time"), profile(working_time="part_time")) is not None
    assert run(cand(working_time="part_time"), profile(working_time="any")) is None


def test_from_raw_job_carries_fields():
    job = RawJob(
        source="adzuna", title="T", company="C", posted_at=TODAY, ad_salary_max=70000,
        work_mode="remote", working_time="full_time", contract_type="permanent",
    )  # fmt: skip
    c = Candidate.from_raw_job(job)
    assert (c.title, c.company, c.posted_at, c.ad_salary_max) == ("T", "C", TODAY, 70000)
    assert (c.work_mode, c.working_time, c.contract_type) == ("remote", "full_time", "permanent")


def test_first_matching_rule_wins():
    p = profile(exclude_keywords=["Werkstudent"], exclude_companies=["Acme"])
    assert run(cand(title="Werkstudent", company="Acme"), p) == "excluded keyword: Werkstudent"
