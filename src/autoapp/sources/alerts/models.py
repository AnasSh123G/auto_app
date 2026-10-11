"""Data model for one job card extracted from an alert email."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AlertSource = Literal[
    "linkedin-alert", "stepstone-alert", "stepstone-alert-recommended", "indeed-alert", "indeed-match",
    "workwise-alert", "llm-alert",
]


class AlertCard(BaseModel):
    """One job posting as shown in an alert email (no description, only card data)."""

    source: AlertSource
    title: str
    title_truncated: bool = False
    company: str
    company_unknown: bool = False  # the email does not name the company; the job page does
    locations: list[str] = Field(default_factory=list)
    locations_truncated: bool = False
    source_job_id: str | None = None
    # LinkedIn: canonical job URL. StepStone: raw tracking link, resolved at ingestion.
    url: str | None = None
    contract_type: str | None = None
    work_mode: str | None = None  # "hybrid" (home office possible), "remote" or None
    working_time: str | None = None  # "full_time", "part_time", "any" or None
    company_size: str | None = None
    # Platform estimates ("geschaetzt"), annual EUR. Never an advertised salary.
    salary_estimate_min: int | None = None
    salary_estimate_max: int | None = None
    badges: list[str] = Field(default_factory=list)
    section: Literal["match", "recommended"] = "match"
    # Redacted text of the card's block in the email (about 1,500 chars) and the email's Message-ID; set at ingestion.
    email_excerpt: str | None = None
    message_id: str | None = None
    # The card (or part of it) was named by the ``alert_extract`` model reading the redacted email text.
    llm_extracted: bool = False
