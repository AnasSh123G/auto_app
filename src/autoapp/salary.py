"""Salary preference resolution (design decision 8)."""

from __future__ import annotations

from dataclasses import dataclass

from autoapp.config.models import Profile


@dataclass(frozen=True)
class SalaryTarget:
    target_eur: int
    min_eur: int | None
    seniority: str | None = None  # the override that was applied, if any
    region: str | None = None  # the federal state whose amount was applied, if any


def resolve_salary_target(profile: Profile, seniority: str | None, location: str | None = None) -> SalaryTarget | None:
    """Return the salary target for a job of the given seniority and location, or None if unconfigured.

    A regional override (the job's federal state) wins over a seniority override, and both replace
    the target and the minimum. If the override has no minimum of its own, the base minimum is kept.
    """
    sal = profile.salary
    if sal is None:
        return None
    if sal.region_overrides:
        from autoapp.geo import german_state

        state = german_state(location)
        regional = sal.region_overrides.get(state) if state else None
        if regional is not None:
            return SalaryTarget(regional.target_eur, regional.min_eur if regional.min_eur is not None else sal.min_eur,
                                region=state)
    key = (seniority or "").strip().lower()
    override = sal.overrides.get(key)  # type: ignore[call-overload]
    if override is None:
        return SalaryTarget(sal.target_eur, sal.min_eur)
    min_eur = override.min_eur if override.min_eur is not None else sal.min_eur
    return SalaryTarget(override.target_eur, min_eur, key)
