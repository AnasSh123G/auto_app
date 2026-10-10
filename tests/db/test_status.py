from __future__ import annotations

import pytest

from autoapp.db.status import CSV_STATUSES, Status


def test_lifecycle_is_exact():
    assert [s.value for s in Status] == [
        "new", "filtered", "description_unavailable", "evaluation_failed", "skipped", "review",
        "approved", "prepared", "drafted", "send_unconfirmed", "sent", "submitted", "interview", "offer",
        "rejected", "withdrawn", "expired",
    ]  # fmt: skip


def test_csv_statuses_are_review_and_later():
    assert [s.value for s in CSV_STATUSES] == [
        "review", "approved", "prepared", "drafted", "send_unconfirmed", "sent", "submitted",
        "interview", "offer", "rejected", "withdrawn", "expired",
    ]  # fmt: skip


def test_parse():
    assert Status.parse(" Sent ") is Status.SENT
    assert Status.parse(Status.OFFER) is Status.OFFER
    with pytest.raises(ValueError):
        Status.parse("maybe later")
    with pytest.raises(ValueError):
        Status("maybe later")
