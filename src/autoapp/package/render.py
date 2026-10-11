"""Render the application folder's text files from Jinja2 templates (design decisions 9 and 10).

HTML templates (``*.html.j2``) are autoescaped; Markdown templates (``*.md.j2``) are not.
Nothing here touches the network or an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jinja2 import Environment, PackageLoader, select_autoescape

from autoapp.evaluation.schema import FinalEvaluation, Requirement
from autoapp.sources.arbeitnow import listing_url
from autoapp.writing.schema import CoverLetter

_ENV = Environment(
    loader=PackageLoader("autoapp.package", "templates"),
    autoescape=select_autoescape(enabled_extensions=("html.j2",), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

NOT_CONFIGURED = "not configured"


class SenderLike(Protocol):
    """Anything with the ``Profile.personal`` address fields."""

    first_name: str
    last_name: str
    email: str
    phone: str
    street: str
    postal_code: str
    city: str


# ------------------------------------------------------------------------------ helpers


def format_eur(value: int | float) -> str:
    """German thousands separator: 55000 -> '55.000'."""
    return f"{int(round(value)):,}".replace(",", ".")


def format_salary_range(low: int | None, high: int | None, period: str | None = "year") -> str | None:
    """'55.000 - 65.000 EUR per year', 'from 55.000 EUR per year', or None when both are missing."""
    unit = {"month": "per month", "year": "per year"}.get(period or "year", "per year")
    if low is not None and high is not None:
        body = f"{format_eur(low)} EUR" if low == high else f"{format_eur(low)} - {format_eur(high)} EUR"
    elif low is not None:
        body = f"from {format_eur(low)} EUR"
    elif high is not None:
        body = f"up to {format_eur(high)} EUR"
    else:
        return None
    return f"{body} {unit}"


# ---------------------------------------------------------------------------- cover letter


def sender_lines(sender: SenderLike) -> list[str]:
    """Name, street, postal code and city, phone, email."""
    return [
        f"{sender.first_name} {sender.last_name}",
        sender.street,
        f"{sender.postal_code} {sender.city}",
        sender.phone,
        sender.email,
    ]


def render_cover_letter_html(
    letter: CoverLetter,
    sender: SenderLike,
    recipient_lines: list[str],
    date_line: str,
) -> str:
    """DIN 5008 style A4 letter as self-contained HTML (no external resources)."""
    return _ENV.get_template("cover_letter.html.j2").render(
        letter=letter,
        sender_lines=[x for x in sender_lines(sender) if x],
        recipient_lines=[x for x in recipient_lines if x and x.strip()],
        date_line=date_line,
        paragraphs=[p.strip() for p in letter.paragraphs if p.strip()],
        lang=letter.language,
    )


# --------------------------------------------------------------------------------- job.md


@dataclass
class JobInfo:
    """Ad snapshot for ``job.md``. Salaries are annual EUR unless a period says otherwise."""

    title: str
    company: str
    description: str
    locations: list[str] = field(default_factory=list)
    job_url: str | None = None
    application_url: str | None = None
    sources: list[str] = field(default_factory=list)
    ad_salary_min: int | None = None  # stated by the employer in the ad
    ad_salary_max: int | None = None
    ad_salary_period: str | None = "year"
    salary_estimate_min: int | None = None  # platform estimate, never an ad range
    salary_estimate_max: int | None = None
    salary_estimate_source: str = "StepStone"
    arbeitnow_url: str | None = None
    no_employer: bool = False  # an Arbeitsagentur job whose employer ad was not found: no application page yet


def render_job_md(job: JobInfo) -> str:
    return _ENV.get_template("job.md.j2").render(
        job=job,
        arbeitnow_url=listing_url(job.arbeitnow_url),
        ad_salary=format_salary_range(job.ad_salary_min, job.ad_salary_max, job.ad_salary_period),
        estimate=format_salary_range(job.salary_estimate_min, job.salary_estimate_max, "year"),
    )


# ---------------------------------------------------------------------------- evaluation.md


@dataclass
class EvaluationExtra:
    """Context printed in ``evaluation.md`` that is not part of the stored evaluation."""

    title: str | None = None
    company: str | None = None
    notes: list[str] = field(default_factory=list)  # additional rule notes, appended to ``evaluation.notes``


def render_evaluation_md(evaluation: FinalEvaluation, extra: EvaluationExtra | None = None) -> str:
    extra = extra or EvaluationExtra()
    reqs: list[Requirement] = evaluation.result.requirements
    return _ENV.get_template("evaluation.md.j2").render(
        ev=evaluation,
        result=evaluation.result,
        extra=extra,
        matched=[r for r in reqs if r.status == "matched"],
        bridgeable=[r for r in reqs if r.status == "bridgeable"],
        missing=[r for r in reqs if r.status == "missing"],
        notes=[*evaluation.notes, *extra.notes],
    )


# ---------------------------------------------------------------------------- form answers


@dataclass
class AnswerItem:
    """One prepared answer: label, copyable value and an optional hint (e.g. the unit)."""

    label: str
    value: str
    hint: str | None = None
    copyable: bool = True


@dataclass
class FormAnswers:
    """Values for common application form fields."""

    sender: SenderLike
    country: str = "Deutschland"
    salary_expectation: int | None = None  # annual gross EUR; None when salary is not configured
    earliest_start: str | None = None
    notice_period: str | None = None
    work_authorization: str | None = None
    language_levels: dict[str, str] = field(default_factory=dict)
    linkedin_url: str | None = None
    xing_url: str | None = None
    motivation: str = ""

    def items(self) -> list[AnswerItem]:
        s = self.sender
        out = [
            AnswerItem("First name", s.first_name),
            AnswerItem("Last name", s.last_name),
            AnswerItem("Email", s.email),
            AnswerItem("Phone", s.phone),
            AnswerItem("Street", s.street),
            AnswerItem("Postal code", s.postal_code),
            AnswerItem("City", s.city),
            AnswerItem("Country", self.country),
        ]
        if self.salary_expectation is None:
            out.append(AnswerItem("Salary expectation", NOT_CONFIGURED, copyable=False))
        else:
            out.append(AnswerItem("Salary expectation", str(self.salary_expectation), "EUR, annual gross"))
        out.append(_optional("Earliest start", self.earliest_start))
        out.append(_optional("Notice period", self.notice_period))
        out.append(_optional("Work authorisation", self.work_authorization))
        if self.language_levels:
            levels = "\n".join(f"{lang}: {level}" for lang, level in self.language_levels.items())
            out.append(AnswerItem("Language levels", levels))
        else:
            out.append(_optional("Language levels", None))
        out.append(_optional("LinkedIn", self.linkedin_url))
        out.append(_optional("XING", self.xing_url))
        if self.motivation.strip():
            out.append(AnswerItem("Motivation", self.motivation.strip(), f"{len(self.motivation.strip())} characters"))
        else:
            out.append(_optional("Motivation", None))
        return out


def _optional(label: str, value: str | None) -> AnswerItem:
    if value and value.strip():
        return AnswerItem(label, value.strip())
    return AnswerItem(label, "not set", copyable=False)


def render_form_answers_md(answers: FormAnswers) -> str:
    return _ENV.get_template("form_answers.md.j2").render(items=answers.items())


# ---------------------------------------------------------------------------------- README

CHANNEL_LABELS = {
    "email": "email",
    "employer_form": "employer form",
    "platform": "job platform",
    "unknown": "unknown",
}


@dataclass
class ReadmeData:
    """Everything ``README.md`` shows. ``files`` are the names inside the folder."""

    title: str
    company: str
    channel: str
    job_id: str
    files: list[str] = field(default_factory=list)
    apply_email: str | None = None
    application_url: str | None = None
    job_url: str | None = None
    reference: str | None = None
    missing_documents: list[str] = field(default_factory=list)
    needs_review_reasons: list[str] = field(default_factory=list)
    assumed_skills: list[str] = field(default_factory=list)
    salary_note: bool = False  # the ad asks for a salary expectation but none is configured
    bundle_size_mb: float | None = None
    bundle_max_mb: float | None = None
    bundle_too_large: bool = False
    warnings: list[str] = field(default_factory=list)  # e.g. a DOCX CV excluded from the bundle
    arbeitnow_url: str | None = None
    no_employer: bool = False  # the employer's ad was not found: the job page is a job ad, not an application page


def render_readme_md(data: ReadmeData) -> str:
    return _ENV.get_template("README.md.j2").render(
        d=data, channel_label=CHANNEL_LABELS.get(data.channel, data.channel),
        arbeitnow_url=listing_url(data.arbeitnow_url),
    )


# ------------------------------------------------------------------------------ apply.html


@dataclass
class FileItem:
    """A local file to upload; ``path`` should be absolute so the ``file://`` link works."""

    label: str
    path: Path


@dataclass
class ApplyData:
    title: str
    company: str
    answers: list[AnswerItem]
    files: list[FileItem] = field(default_factory=list)
    job_url: str | None = None
    application_url: str | None = None
    arbeitnow_url: str | None = None
    no_employer: bool = False


def render_apply_html(data: ApplyData) -> str:
    """Offline helper page: links, copy buttons and file paths. No external resources."""
    return _ENV.get_template("apply.html.j2").render(
        d=data,
        arbeitnow_url=listing_url(data.arbeitnow_url),
        files=[(f.label, str(f.path), f.path.resolve().as_uri()) for f in data.files],
    )
