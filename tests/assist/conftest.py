"""Shared fixtures for the assisted-fill tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from autoapp.assist.mapper import FillValues

FORMS = Path(__file__).resolve().parent.parent / "fixtures" / "forms"


@pytest.fixture
def forms() -> Path:
    return FORMS


@pytest.fixture
def values(tmp_path: Path) -> FillValues:
    files = {}
    for name in ("cv.pdf", "cover_letter.pdf", "Bewerbungsmappe.pdf", "zeugnis1.pdf", "zeugnis2.pdf"):
        p = tmp_path / name
        p.write_bytes(b"%PDF-1.4 test")
        files[name] = p
    return FillValues(
        first_name="Max",
        last_name="Mustermann",
        email="max@example.com",
        phone="+49 170 1234567",
        street="Musterstraße 1",
        postal_code="10115",
        city="Berlin",
        country="Deutschland",
        linkedin_url="https://linkedin.com/in/max",
        xing_url="https://xing.com/profile/max",
        salary_expectation="65.000 EUR",
        earliest_start="01.12.2026",
        notice_period="3 Monate",
        work_authorization="EU-Bürger",
        language_levels={"German": "native", "English": "C1"},
        cv=files["cv.pdf"],
        cover_letter_pdf=files["cover_letter.pdf"],
        certificates=[files["zeugnis1.pdf"], files["zeugnis2.pdf"]],
        bundle_pdf=files["Bewerbungsmappe.pdf"],
        motivation="Ich bewerbe mich, weil die Stelle perfekt passt.",
    )


@pytest.fixture(scope="module")
def browser_ctx() -> Iterator:
    """A headless Chromium context shared per module (skipped via the ``browser`` marker)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        yield ctx
        browser.close()


@pytest.fixture
def page(browser_ctx):
    pg = browser_ctx.new_page()
    yield pg
    pg.close()
