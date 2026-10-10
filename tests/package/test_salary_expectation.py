from __future__ import annotations

import pytest

from autoapp.package.salary_expectation import compute_expectation, needs_salary_note
from autoapp.salary import SalaryTarget


def tgt(target=72000, min_eur=None):
    return SalaryTarget(target, min_eur)


def test_target_above_range_clamped_down():
    assert compute_expectation(tgt(72000, 58000), 55000, 65000) == 65000


def test_no_range_uses_target_rounded():
    assert compute_expectation(tgt(72400), None, None) == 72000
    assert compute_expectation(tgt(72000, 58000), None, None) == 72000


def test_unconfigured_returns_none():
    assert compute_expectation(None, 55000, 65000) is None
    assert compute_expectation(None, None, None) is None


def test_target_below_range_min_raised_to_range_min():
    assert compute_expectation(tgt(50000), 55000, 65000) == 55000


def test_profile_min_above_clamped_value_wins():
    assert compute_expectation(tgt(60000, 62000), 50000, 58000) == 62000


def test_one_sided_ranges():
    assert compute_expectation(tgt(72000), None, 65000) == 65000
    assert compute_expectation(tgt(72000), None, 80000) == 72000
    assert compute_expectation(tgt(50000), 55000, None) == 55000
    assert compute_expectation(tgt(72000), 55000, None) == 72000


@pytest.mark.parametrize("target,expected", [(71499, 71000), (71500, 72000), (71000, 71000), (71999, 72000)])
def test_rounding_half_up(target, expected):
    assert compute_expectation(tgt(target), None, None) == expected


def test_needs_salary_note():
    assert needs_salary_note(None, True)
    assert not needs_salary_note(None, False)
    assert not needs_salary_note(65000, True)
