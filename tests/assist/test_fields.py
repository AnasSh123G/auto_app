"""Field collector tests against the HTML fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoapp.assist.fields import collect_fields

pytestmark = pytest.mark.browser


def test_plain_form_fields(page, forms: Path) -> None:
    page.goto((forms / "plain.html").as_uri())
    fields = collect_fields(page)
    by_label = {f.label: f for f in fields}
    assert {"Vorname *", "Nachname *", "E-Mail *", "Telefon", "Straße", "PLZ", "Ort"} <= set(by_label)
    assert "Gehaltsvorstellung (brutto/Jahr)" in by_label
    assert by_label["Vorname *"].required and by_label["Vorname *"].maxlength == 40
    assert by_label["Vorname *"].type == "text" and by_label["E-Mail *"].type == "email"
    assert by_label["Telefon"].type == "tel" and not by_label["Telefon"].required
    assert by_label["PLZ"].maxlength == 5 and by_label["Straße"].maxlength is None
    assert by_label["Ort"].label == "Ort"  # wrapping label, nested input text excluded
    assert by_label["Lebenslauf *"].type == "file" and by_label["Anschreiben"].type == "file"
    quelle = by_label["Wie haben Sie von uns erfahren? *"]
    assert quelle.type == "textarea" and quelle.required and quelle.maxlength == 300
    dsgvo = by_label["Ich habe die Datenschutzerklärung gelesen"]
    assert dsgvo.type == "checkbox" and dsgvo.required
    # submit button is not a field; ids and selectors are unique
    assert not any(f.type in ("submit", "button") for f in fields)
    assert len({f.field_id for f in fields}) == len(fields)
    for f in fields:
        assert page.locator(f.selector).count() == 1


def test_collect_is_stable_and_skips_invisible(page, forms: Path) -> None:
    page.goto((forms / "multistep.html").as_uri())
    first = collect_fields(page)
    assert [f.label for f in first] == ["Vorname", "Nachname", "E-Mail"]  # step 2 hidden
    again = collect_fields(page)
    assert [f.field_id for f in again] == [f.field_id for f in first]


def test_iframe_fields_are_collected(page, forms: Path) -> None:
    page.goto((forms / "iframe_host.html").as_uri())
    page.wait_for_selector("iframe")
    page.frame_locator("iframe").locator("#fn").wait_for()
    fields = collect_fields(page)
    assert fields and all(f.frame_index == 1 for f in fields)
    by_label = {f.label: f for f in fields}
    assert {"First name", "Last name", "Email", "Phone", "Salary expectation", "Resume"} <= set(by_label)
    assert by_label["First name"].required
    assert by_label["Why do you want to work with us?"].maxlength == 500
    country = by_label["Country"]
    assert country.type == "select"
    assert country.options == [("", "Please select"), ("DE", "Germany"), ("AT", "Austria")]
    assert page.frames[1].locator(by_label["Email"].selector).count() == 1


def test_label_resolution_order(page) -> None:
    page.set_content(
        """
        <label for="a">Via for</label><input id="a" aria-label="ignored">
        <label>Wrapping <input id="b"></label>
        <input id="c" aria-label="Aria one">
        <span id="lbl">Labelled by</span><input id="d" aria-labelledby="lbl">
        <input id="e" placeholder="Holder">
        <input id="f" name="only_name">
        <div>Preceding text</div><div><input id="g"></div>
        <fieldset><legend>Wie alt?</legend>
          <label><input type="radio" name="age" value="u"> unter 30</label>
          <label><input type="radio" name="age" value="o"> über 30</label></fieldset>
        """
    )
    labels = {f.html_id or f.name: f.label for f in collect_fields(page)}
    assert labels["a"] == "Via for"
    assert labels["b"] == "Wrapping"
    assert labels["c"] == "Aria one"
    assert labels["d"] == "Labelled by"
    assert labels["e"] == "Holder"
    assert labels["f"] == "only_name"
    assert labels["g"] == "Preceding text"
    radios = [f for f in collect_fields(page) if f.type == "radio"]
    assert len(radios) == 1
    assert radios[0].label == "Wie alt?"
    assert radios[0].options == [("u", "unter 30"), ("o", "über 30")]


def test_hidden_inputs_and_invisible_elements_skipped(page) -> None:
    page.set_content(
        """
        <input type="hidden" name="csrf"><input type="submit" value="x"><input type="button">
        <input id="vis"><input id="gone" style="display:none"><div style="display:none"><input id="nested"></div>
        <input id="dis" disabled>
        """
    )
    assert [f.html_id for f in collect_fields(page)] == ["vis"]
