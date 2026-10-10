"""Federal state detection and the regional salary amount (52000, 56000 in Bayern)."""

from __future__ import annotations

import pytest

from autoapp.config.models import Personal, Profile, SalaryOverride, SalaryPrefs
from autoapp.geo import german_state
from autoapp.package.salary_expectation import compute_expectation
from autoapp.salary import resolve_salary_target


@pytest.mark.parametrize("location,state", [
    ("München", "bayern"), ("Munich, Bavaria, Germany", "bayern"), ("Greater Munich Metropolitan Area", "bayern"),
    ("85748 Garching bei München", "bayern"), ("Nürnberg", "bayern"), ("Aschaffenburg", "bayern"),
    ("90402 Nürnberg", "bayern"), ("Bayern", "bayern"), ("Würzburg, Bayern", "bayern"),
    ("Köln", None), ("Bochum, Nordrhein-Westfalen", "nordrhein-westfalen"), ("Berlin", "berlin"),
    ("Lower Saxony, Germany", "niedersachsen"), ("Saxony-Anhalt", "sachsen-anhalt"), ("Dresden, Sachsen", "sachsen"),
    ("Stuttgart", None), ("70173 Stuttgart", None), ("Frankfurt am Main", None), ("Germany", None), ("", None), (None, None),
    ("44801 Bochum", None), ("Neu-Ulm", "bayern"), ("Ulm", None),
])
def test_state_from_location_line(location, state):
    assert german_state(location) == state


def profile(**salary) -> Profile:
    personal = Personal(first_name="A", last_name="B", email="a@b.de", phone="1", street="s", postal_code="1", city="c")
    return Profile(personal=personal, salary=SalaryPrefs(**salary))


def test_52000_by_default_and_56000_in_bayern():
    p = profile(target_eur=52000, region_overrides={"bayern": SalaryOverride(target_eur=56000)})
    assert compute_expectation(resolve_salary_target(p, "mid", "Bochum"), None, None) == 52000
    assert compute_expectation(resolve_salary_target(p, "mid", None), None, None) == 52000
    munich = resolve_salary_target(p, "mid", "München, Bayern")
    assert munich.region == "bayern" and compute_expectation(munich, None, None) == 56000


def test_region_wins_over_seniority_and_keeps_the_base_minimum():
    p = profile(target_eur=52000, min_eur=48000, overrides={"junior": SalaryOverride(target_eur=50000)},
                region_overrides={"bayern": SalaryOverride(target_eur=56000)})
    assert resolve_salary_target(p, "junior", "Nürnberg").target_eur == 56000
    assert resolve_salary_target(p, "junior", "Nürnberg").min_eur == 48000
    assert resolve_salary_target(p, "junior", "Köln").target_eur == 50000


def test_an_employer_stated_range_still_applies_and_unknown_states_are_refused():
    p = profile(target_eur=52000, region_overrides={"bayern": SalaryOverride(target_eur=56000)})
    assert compute_expectation(resolve_salary_target(p, "mid", "München"), None, 54000) == 54000
    with pytest.raises(ValueError, match="unknown federal state"):
        profile(target_eur=52000, region_overrides={"bavaria": SalaryOverride(target_eur=56000)})
