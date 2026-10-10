"""Filler tests: fill, consent guard, multi-step, iframe, restricted domains, source guard."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoapp.assist import filler as filler_mod
from autoapp.assist import mapper as mapper_mod
from autoapp.assist.filler import CONSENT_PATTERN, fill_page, is_restricted
from autoapp.assist.mapper import Assignment, FillValues

RESTRICTED = ["linkedin.com", "indeed.com", "indeed.de", "stepstone.de"]


def val(page, selector: str) -> str:
    return page.input_value(selector)


def file_count(page, selector: str) -> int:
    return page.evaluate("s => document.querySelector(s).files.length", selector)


# ----------------------------------------------------------------------- pure tests


def test_is_restricted() -> None:
    assert is_restricted("https://www.linkedin.com/jobs/view/1/", RESTRICTED)
    assert is_restricted("https://linkedin.com/x", RESTRICTED)
    assert is_restricted("https://de.indeed.com/viewjob", RESTRICTED)
    assert is_restricted("https://www.stepstone.de/job", RESTRICTED)
    assert not is_restricted("https://notlinkedin.com/", RESTRICTED)
    assert not is_restricted("https://example.com/linkedin.com", RESTRICTED)
    assert not is_restricted("file:///tmp/plain.html", RESTRICTED)
    assert not is_restricted("https://www.linkedin.com/", [])


@pytest.mark.parametrize(
    "label",
    [
        "Ich habe die Datenschutzerklärung gelesen", "I accept the privacy policy", "Ich willige ein (Einwilligung)",
        "I give my consent", "AGB akzeptieren", "I agree to the terms", "Ich stimme zu", "Ich bestätige die Richtigkeit",
        "Bitte akzeptieren Sie", "Ich bestaetige",
    ],
)
def test_consent_pattern_matches(label: str) -> None:
    assert CONSENT_PATTERN.search(label)


def test_consent_pattern_ignores_normal_labels() -> None:
    assert not CONSENT_PATTERN.search("Newsletter abonnieren")
    assert not CONSENT_PATTERN.search("Ich besitze einen Führerschein")


def test_no_forbidden_calls_in_source() -> None:
    """The fill code must have no code path that clicks, presses Enter or sends a form."""
    forbidden = [r"\.click\(", r"press\(", r"submit", r"dispatch_event", r"\.tap\(", r"dblclick", r"\.keyboard"]
    for mod in (filler_mod, mapper_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for pat in forbidden:
            assert not re.search(pat, src, re.IGNORECASE), f"{Path(mod.__file__).name} contains {pat!r}"


# -------------------------------------------------------------------- browser tests

browser = pytest.mark.browser


@browser
def test_fill_plain_form(page, forms: Path, values: FillValues) -> None:
    page.goto((forms / "plain.html").as_uri())
    report = fill_page(page, values, restricted_domains=RESTRICTED)

    assert not report.skipped_restricted
    assert val(page, "#vorname") == "Max"
    assert val(page, "#nachname") == "Mustermann"
    assert val(page, "#email") == "max@example.com"
    assert val(page, "#tel") == "+49 170 1234567"
    assert val(page, "#strasse") == "Musterstraße 1"
    assert val(page, "#plz") == "10115"
    assert val(page, "input[name=ort]") == "Berlin"
    assert val(page, "#gehalt") == "65.000 EUR"
    assert val(page, "#eintritt") == "01.12.2026"
    assert file_count(page, "#cv") == 1
    assert file_count(page, "#anschreiben") == 1
    assert page.evaluate("document.querySelector('#cv').files[0].name") == "cv.pdf"
    assert page.evaluate("document.querySelector('#anschreiben').files[0].name") == "cover_letter.pdf"

    # consent is left to the user; "how did you hear about us" is always answered with LinkedIn
    assert not page.is_checked("#datenschutz")
    assert val(page, "#quelle") == "LinkedIn"
    assert "Ich habe die Datenschutzerklärung gelesen" in report.needs_user
    assert "Wie haben Sie von uns erfahren? *" in report.filled
    assert not any("Vorname" in n for n in report.needs_user)
    assert "Vorname *" in report.filled and "Gehaltsvorstellung (brutto/Jahr)" in report.filled

    # highlighting
    assert page.get_attribute("#vorname", "data-autoapp-state") == "filled"
    assert page.get_attribute("#datenschutz", "data-autoapp-state") == "needs"
    assert page.get_attribute("#quelle", "data-autoapp-state") == "filled"
    outline = page.evaluate("getComputedStyle(document.querySelector('#datenschutz')).outlineColor")
    assert outline == "rgb(214, 40, 40)"
    outline = page.evaluate("getComputedStyle(document.querySelector('#vorname')).outlineColor")
    assert outline == "rgb(26, 156, 60)"

    # nothing was submitted
    assert page.evaluate("window.__submits") == 0
    assert "send the application yourself" in report.message


@browser
def test_refill_keeps_existing_user_values(page, forms: Path, values: FillValues) -> None:
    page.goto((forms / "plain.html").as_uri())
    page.fill("#vorname", "Erika")
    report = fill_page(page, values, restricted_domains=RESTRICTED)
    assert val(page, "#vorname") == "Erika"
    assert "Vorname *" not in report.filled


@browser
def test_maxlength_respected_when_filling(page, forms: Path, values: FillValues) -> None:
    page.goto((forms / "plain.html").as_uri())
    values.first_name = "Alexander " * 6
    fill_page(page, values, restricted_domains=RESTRICTED)
    assert len(val(page, "#vorname")) <= 40


@browser
def test_extra_assignments_fill_but_never_consent(page, forms: Path, values: FillValues) -> None:
    from autoapp.assist.fields import collect_fields

    page.set_content(
        '<form><p><label for="farbe">Lieblingsfarbe *</label> <textarea id="farbe" name="farbe" required></textarea></p>'
        '<p><input id="datenschutz" name="datenschutz" type="checkbox" required>'
        '<label for="datenschutz">Ich habe die Datenschutzerklärung gelesen</label></p></form>')
    ids = {f.html_id: f.field_id for f in collect_fields(page)}
    extra = [
        Assignment(ids["farbe"], "fill", "Blau"),
        Assignment(ids["datenschutz"], "check", "on"),
    ]
    report = fill_page(page, values, restricted_domains=RESTRICTED, extra_assignments=extra)
    assert val(page, "#farbe") == "Blau"
    assert not page.is_checked("#datenschutz")
    assert "Lieblingsfarbe *" not in report.needs_user
    assert "Ich habe die Datenschutzerklärung gelesen" in report.needs_user


@browser
def test_multistep_fill_twice(page, forms: Path, values: FillValues) -> None:
    page.goto((forms / "multistep.html").as_uri())
    r1 = fill_page(page, values, restricted_domains=RESTRICTED)
    assert val(page, "#vorname") == "Max" and val(page, "#email") == "max@example.com"
    assert val(page, "#gehalt") == ""  # step 2 not visible yet
    assert r1.skipped_restricted is False

    page.click("#weiter")  # the test (the user) clicks, never the library

    r2 = fill_page(page, values, restricted_domains=RESTRICTED)
    assert val(page, "#gehalt") == "65.000 EUR"
    assert file_count(page, "#lebenslauf") == 1
    assert not page.is_checked("#dsgvo")
    assert "Ich stimme der Verarbeitung meiner Daten zu" in r2.needs_user
    assert "Vorname" not in r2.filled  # step 1 values are kept, not refilled
    assert page.evaluate("window.__submits") == 0


@browser
def test_single_upload_gets_bundle(page, forms: Path, values: FillValues) -> None:
    page.goto((forms / "single_upload.html").as_uri())
    fill_page(page, values, restricted_domains=RESTRICTED)
    assert file_count(page, "#mappe") == 1
    assert page.evaluate("document.querySelector('#mappe').files[0].name") == "Bewerbungsmappe.pdf"
    assert val(page, "#vorname") == "Max"
    assert page.evaluate("window.__submits") == 0


@browser
def test_iframe_form_is_filled(page, forms: Path, values: FillValues) -> None:
    page.goto((forms / "iframe_host.html").as_uri())
    frame_loc = page.frame_locator("iframe")
    frame_loc.locator("#fn").wait_for()
    report = fill_page(page, values, restricted_domains=RESTRICTED)
    assert frame_loc.locator("#fn").input_value() == "Max"
    assert frame_loc.locator("#em").input_value() == "max@example.com"
    assert frame_loc.locator("#sal").input_value() == "65.000 EUR"
    assert frame_loc.locator("#country").input_value() == "DE"
    assert frame_loc.locator("#resume").evaluate("e => e.files.length") == 1
    assert not frame_loc.locator("#terms").is_checked()
    assert "I agree to the terms" in report.needs_user
    assert frame_loc.locator("#fn").get_attribute("data-autoapp-state") == "filled"
    assert page.frames[1].evaluate("window.__submits") == 0
    assert page.evaluate("window.__submits") == 0


@browser
def test_restricted_domain_fills_nothing(browser_ctx, forms: Path, values: FillValues) -> None:
    plain = forms / "plain.html"
    browser_ctx.route("https://www.linkedin.com/**", lambda route: route.fulfill(path=str(plain)))
    pg = browser_ctx.new_page()
    try:
        pg.goto("https://www.linkedin.com/jobs/view/1/")
        report = fill_page(pg, values, restricted_domains=RESTRICTED)
        assert report.skipped_restricted
        assert report.filled == [] and report.needs_user == []
        assert "apply.html" in report.message
        assert val(pg, "#vorname") == "" and val(pg, "#email") == ""
        assert file_count(pg, "#cv") == 0
        assert pg.get_attribute("#vorname", "data-autoapp-id") is None  # page untouched
    finally:
        pg.close()
        browser_ctx.unroute("https://www.linkedin.com/**")


@browser
def test_restricted_checked_on_every_fill(browser_ctx, forms: Path, values: FillValues) -> None:
    """A page that navigates into a restricted domain between fills is caught by the second fill."""
    plain = forms / "plain.html"
    browser_ctx.route("https://www.linkedin.com/**", lambda route: route.fulfill(path=str(plain)))
    pg = browser_ctx.new_page()
    try:
        pg.goto(plain.as_uri())
        assert not fill_page(pg, values, restricted_domains=RESTRICTED).skipped_restricted
        pg.goto("https://www.linkedin.com/jobs/view/2/")
        assert fill_page(pg, values, restricted_domains=RESTRICTED).skipped_restricted
        assert val(pg, "#vorname") == ""
    finally:
        pg.close()
        browser_ctx.unroute("https://www.linkedin.com/**")


@browser
def test_radio_select_and_consent_radio(page, values: FillValues) -> None:
    values.salutation = "Herr"
    page.set_content(
        """
        <fieldset><legend>Anrede</legend>
          <label><input type="radio" name="anrede" value="f"> Frau</label>
          <label><input type="radio" name="anrede" value="m"> Herr</label></fieldset>
        <label for="land">Land</label>
        <select id="land"><option value="">-</option><option value="de">Deutschland</option></select>
        <fieldset><legend>Datenschutz akzeptieren?</legend>
          <label><input type="radio" name="ds" value="y"> Ja</label>
          <label><input type="radio" name="ds" value="n"> Nein</label></fieldset>
        """
    )
    report = fill_page(page, values, restricted_domains=RESTRICTED)
    assert page.is_checked("input[name=anrede][value=m]")
    assert not page.is_checked("input[name=anrede][value=f]")
    assert page.input_value("#land") == "de"
    assert not page.is_checked("input[name=ds][value=y]")
    assert not page.is_checked("input[name=ds][value=n]")
    assert "Datenschutz akzeptieren?" in report.needs_user


@browser
def test_model_choices_replace_the_rules_facts_but_not_fixed_answers(page, values: FillValues) -> None:
    from autoapp.assist.fields import collect_fields

    page.set_content(
        '<form><p><label for="ort">Stadt</label> <input id="ort" name="ort"></p>'
        '<p><label for="quelle">Wie hast du von uns erfahren?</label> <input id="quelle" name="quelle"></p></form>')
    ids = {f.html_id: f.field_id for f in collect_fields(page)}
    values.city = "Berlin"
    chosen = [Assignment(ids["ort"], "fill", "Karlsruhe"), Assignment(ids["quelle"], "fill", "Zeitung")]
    fill_page(page, values, restricted_domains=RESTRICTED, override_assignments=chosen)
    assert val(page, "#ort") == "Karlsruhe"
    assert val(page, "#quelle") == "LinkedIn"  # the fixed answer wins
