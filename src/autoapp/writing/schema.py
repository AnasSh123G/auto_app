"""What the writer model returns, and the assembled letter (design decision 9).

The model writes only the prose and lists its claims. The subject, salutation and
closing are deterministic (``autoapp.writing.letter_format``) so formatting never
depends on the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

ClaimType = Literal["cv", "profile", "instructions", "assumed_basic"]


class Claim(BaseModel):
    text: str = Field(description="The factual statement as it appears in the letter (short).")
    type: ClaimType = Field(
        description="cv / profile / instructions: backed by that source. assumed_basic: basic familiarity "
        "with a bridgeable requirement that no source mentions."
    )
    evidence: str | None = Field(
        default=None,
        description="For cv/profile/instructions: the exact words from the source, in the source's language. "
        "Several quotes from different places are separated by ' | '. Never paraphrase or translate.",
    )
    requirement: str | None = Field(
        default=None, description="For assumed_basic: the bridgeable requirement exactly as listed."
    )


class LetterDraft(BaseModel):
    paragraphs: list[str] = Field(description="Three or four body paragraphs. No salutation, no closing.")
    motivation_short: str = Field(description="Short motivation text for application forms, max 1000 characters.")
    email_body: str = Field(description="Short email text that accompanies the attached application.")
    claims: list[Claim] = Field(
        description="Every factual statement about the candidate in paragraphs, motivation or email. "
        "Sentences that only name the position, the company or the attachments are not claims."
    )


@dataclass
class CoverLetter:
    """The finished letter: deterministic frame plus the model's prose."""

    language: str
    subject: str
    salutation: str
    paragraphs: list[str]
    closing: str
    signature: str
    motivation_short: str
    email_body: str
    claims: list[Claim] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n\n".join(p.strip() for p in self.paragraphs)

    @property
    def assumed_skills(self) -> list[str]:
        return [c.requirement or c.text for c in self.claims if c.type == "assumed_basic"]

    def to_markdown(self) -> str:
        """Editable form saved as ``cover_letter.md``; ``render`` reads it back."""
        return (
            f"# {self.subject}\n\n{self.salutation}\n\n{self.body}\n\n{self.closing}\n{self.signature}\n"
        )


class FreeTextAnswer(BaseModel):
    """Answer to one free-text question in an application form (assisted fill)."""

    answer: str = Field(description="The answer, in the requested language, within the character limit.")
    claims: list[Claim] = Field(
        description="Every factual statement about the candidate in the answer, typed and with exact "
        "evidence (several quotes separated by ' | ')."
    )
