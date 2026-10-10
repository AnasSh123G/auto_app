"""Normalised job record produced by every source adapter, and the ``JobSource`` protocol."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class RawJob(BaseModel):
    """One listing as reported by a source, before deduplication.

    ``ad_salary_*`` holds only employer-stated annual EUR figures; platform guesses go to
    ``salary_estimate_*`` and never drive filtering.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    source_job_id: str | None = None
    title: str
    company: str
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    url: str | None = None
    application_url: str | None = None
    description: str | None = None
    posted_at: date | None = None
    ad_salary_min: int | None = None
    ad_salary_max: int | None = None
    salary_estimate_min: int | None = None
    salary_estimate_max: int | None = None
    work_mode: str | None = None
    working_time: str | None = None
    contract_type: str | None = None
    contact_fields: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    search_profile: str | None = None
    section: Literal["match", "recommended"] | None = None


@runtime_checkable
class JobSource(Protocol):
    """A job source. ``fetch_detail`` is optional; ``skip_reason`` returns text when unusable."""

    name: str

    def search(self, profile: Any) -> Iterable[RawJob]:
        """Yield jobs for one ``SearchProfile``, sending only the filters it configures."""
        ...
