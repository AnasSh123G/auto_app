"""The job status lifecycle (application-tracking spec)."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """Every status a job can have. Unknown strings raise ``ValueError`` on parsing."""

    NEW = "new"
    FILTERED = "filtered"
    DESCRIPTION_UNAVAILABLE = "description_unavailable"
    EVALUATION_FAILED = "evaluation_failed"
    SKIPPED = "skipped"
    REVIEW = "review"
    APPROVED = "approved"
    PREPARED = "prepared"
    DRAFTED = "drafted"  # saved as a draft in the user's mailbox (review-first runs)
    SEND_UNCONFIRMED = "send_unconfirmed"
    SENT = "sent"
    SUBMITTED = "submitted"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"

    @classmethod
    def parse(cls, value: str | Status) -> Status:
        """Parse a status string (case and surrounding whitespace are ignored)."""
        if isinstance(value, Status):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            valid = ", ".join(s.value for s in cls)
            raise ValueError(f"unknown status {value!r}; valid statuses: {valid}") from None


# Statuses that get a row in applications.csv: ``review`` and everything after it.
CSV_STATUSES: tuple[Status, ...] = (
    Status.REVIEW,
    Status.APPROVED,
    Status.PREPARED,
    Status.DRAFTED,
    Status.SEND_UNCONFIRMED,
    Status.SENT,
    Status.SUBMITTED,
    Status.INTERVIEW,
    Status.OFFER,
    Status.REJECTED,
    Status.WITHDRAWN,
    Status.EXPIRED,
)
