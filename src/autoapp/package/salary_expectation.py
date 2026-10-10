"""Salary expectation rule (design decision 8).

``round_1000(max(profile_min or 0, clamp(target, ad_min, ad_max)))``
"""

from __future__ import annotations

from autoapp.salary import SalaryTarget


def round_1000(value: float) -> int:
    """Round to the nearest 1000, halves up."""
    return int((value + 500) // 1000 * 1000)


def compute_expectation(target: SalaryTarget | None, ad_min: int | None, ad_max: int | None) -> int | None:
    """Annual gross salary expectation in EUR, or None when salary is not configured.

    ``ad_min`` and ``ad_max`` MUST be the range stated by the employer in the ad
    (``ad_salary_min`` / ``ad_salary_max``). Platform estimates (``salary_estimate_*``, e.g.
    StepStone's "geschätzt") must never be passed: they are not an ad range, so the expectation
    is then computed as if the ad stated none. A one-sided range clamps on that side only.
    """
    if target is None:
        return None
    value = target.target_eur
    if ad_max is not None:
        value = min(value, ad_max)
    if ad_min is not None:
        value = max(value, ad_min)
    value = max(target.min_eur or 0, value)
    return round_1000(value)


def needs_salary_note(expectation: int | None, ad_asks_salary: bool) -> bool:
    """True when the ad asks for a salary expectation but none can be given (README note)."""
    return ad_asks_salary and expectation is None
