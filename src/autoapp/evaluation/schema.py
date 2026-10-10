"""What the LLM returns when it evaluates a job (design decision 8).

The model judges; code decides. ``EvaluationResult`` is the raw judgement. The final
decision, the bridgeable re-check and the email grounding are applied by
``autoapp.evaluation.rules``.

The schema avoids numeric/length constraints so every provider's structured-output
mode accepts it; ranges are enforced in code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Proficiency = Literal["basic", "intermediate", "advanced", "unspecified"]
RequirementStatus = Literal["matched", "bridgeable", "missing"]
Seniority = Literal["junior", "mid", "senior", "lead", "unknown"]
GermanLevel = Literal["none", "A1", "A2", "B1", "B2", "C1", "C2", "native", "unknown"]
WorkMode = Literal["onsite", "hybrid", "remote", "unknown"]
Channel = Literal["email", "employer_form", "platform", "unknown"]
Language = Literal["de", "en"]
RequestedDocument = Literal["cv", "cover_letter", "certificates", "references", "portfolio", "photo", "other"]
Decision = Literal["apply", "maybe", "skip"]


class Requirement(BaseModel):
    text: str = Field(description="The requirement as the ad states it, shortened if long.")
    proficiency: Proficiency = Field(
        description="Level the ad asks for: basic (Grundkenntnisse, first experience), intermediate "
        "(solid/sehr gute Kenntnisse), advanced (expert, mehrjährig, deep, lead), or unspecified."
    )
    core: bool = Field(description="True if the role cannot be done without it (e.g. Java for a Java developer).")
    years_required: int | None = Field(default=None, description="Years of experience the ad names for it, if any.")
    status: RequirementStatus = Field(
        description="matched: CV or profile shows it. bridgeable: absent, but basic/unspecified and not core. "
        "missing: absent and not bridgeable."
    )
    evidence: str | None = Field(default=None, description="Short quote from the CV/profile when matched.")


class SalaryRange(BaseModel):
    min_eur: int | None = None
    max_eur: int | None = None
    period: Literal["year", "month"] | None = None


class EvaluationResult(BaseModel):
    fit_score: int = Field(description="0-100. Bridgeable gaps weigh much less than missing ones.")
    rationale: str = Field(description="Two to four sentences.")
    requirements: list[Requirement]
    dealbreakers: list[str] = Field(description="Hard blockers against the candidate's profile; empty if none.")
    seniority: Seniority
    german_level_required: GermanLevel
    work_mode: WorkMode
    channel: Channel = Field(
        description="email if the ad asks for applications by email; employer_form for an employer/ATS form; "
        "platform for applying on the job board itself; unknown otherwise."
    )
    apply_email: str | None = Field(default=None, description="Exactly as written in the ad, or null.")
    application_url: str | None = None
    contact_person: str | None = Field(default=None, description="e.g. 'Frau Weber', or null.")
    reference: str | None = Field(default=None, description="Kennziffer / reference number, or null.")
    requested_documents: list[RequestedDocument]
    language: Language = Field(description="Language the ad is written in.")
    ad_salary: SalaryRange | None = Field(
        default=None, description="Only a salary the employer states in the ad; never an estimate."
    )
    full_title: str | None = Field(default=None, description="The job title as the ad states it.")


class FinalEvaluation(BaseModel):
    """The stored evaluation after code-side rules ran."""

    result: EvaluationResult
    decision: Decision
    fit_score: int
    notes: list[str] = Field(default_factory=list, description="What the rules changed, for transparency.")
