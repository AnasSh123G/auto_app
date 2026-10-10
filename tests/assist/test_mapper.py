"""Pure mapper tests (no browser)."""

from __future__ import annotations

import dataclasses

import pytest

from autoapp.assist.fields import FieldInfo
from autoapp.assist.mapper import FillValues, map_fields, norm, truncate_at_word


def mk(label: str, type: str = "text", **kw) -> FieldInfo:
    kw.setdefault("field_id", "f0-" + str(abs(hash((label, type, kw.get("name", ""))))))
    return FieldInfo(
        frame_index=0, selector="[x]", tag="textarea" if type == "textarea" else "input", type=type, label=label, **kw
    )


def one(label: str, values: FillValues, type: str = "text", **kw):
    f = mk(label, type, **kw)
    res = map_fields([f], values)
    assert len(res.assignments) + len(res.unmatched) == 1
    return res.assignments[0] if res.assignments else None


def test_norm() -> None:
    assert norm("Straße *") == "strasse"
    assert norm("Vorname: (optional)") == "vorname"
    assert norm("Verfügbarkeit") == "verfuegbarkeit"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Vorname", "Max"), ("Vorname *", "Max"), ("First name", "Max"), ("Given name:", "Max"),
        ("Nachname", "Mustermann"), ("Last Name", "Mustermann"), ("Surname", "Mustermann"),
        ("Family name (optional)", "Mustermann"),
        ("Name", "Max Mustermann"), ("Full name", "Max Mustermann"), ("Vor- und Nachname", "Max Mustermann"),
        ("E-Mail", "max@example.com"), ("Email *", "max@example.com"), ("E-Mail-Adresse", "max@example.com"),
        ("Telefon", "+49 170 1234567"), ("Mobil", "+49 170 1234567"), ("Phone number", "+49 170 1234567"),
        ("Straße", "Musterstraße 1"), ("Strasse und Hausnummer", "Musterstraße 1"), ("Street", "Musterstraße 1"),
        ("Address line 1", "Musterstraße 1"),
        ("PLZ", "10115"), ("Postleitzahl", "10115"), ("Postal code", "10115"), ("ZIP", "10115"),
        ("Ort", "Berlin"), ("Stadt", "Berlin"), ("City", "Berlin"), ("Wohnort", "Berlin"),
        ("Land", "Deutschland"), ("Country", "Deutschland"),
        ("Gehaltsvorstellung (brutto/Jahr)", "65.000 EUR"), ("Gehaltswunsch", "65.000 EUR"),
        ("Salary expectation", "65.000 EUR"), ("Desired salary", "65.000 EUR"),
        ("Eintrittstermin", "01.12.2026"), ("Frühester Eintritt", "01.12.2026"), ("Verfügbarkeit", "01.12.2026"),
        ("Earliest start", "01.12.2026"), ("Start date", "01.12.2026"), ("Availability", "01.12.2026"),
        ("Kündigungsfrist", "3 Monate"), ("Notice period", "3 Monate"),
        ("LinkedIn", "https://linkedin.com/in/max"), ("LinkedIn-Profil", "https://linkedin.com/in/max"),
        ("XING", "https://xing.com/profile/max"),
        ("Arbeitserlaubnis", "EU-Bürger"),
        ("Deutschkenntnisse", "native"), ("English level", "C1"),
    ],
)
def test_text_label_variants(label: str, expected: str, values: FillValues) -> None:
    a = one(label, values)
    assert a is not None, label
    assert (a.kind, a.value) == ("fill", expected)


def test_name_and_id_attributes_used_when_label_is_unhelpful(values: FillValues) -> None:
    a = one("Feld 1", values, name="firstName")
    assert a is not None and a.value == "Max"
    a = one("Feld 2", values, name="x", html_id="postal_code")
    assert a is not None and a.value == "10115"


def test_type_fallback(values: FillValues) -> None:
    a = one("Kontakt", values, type="email")
    assert a is not None and a.value == "max@example.com"


def test_unknown_field_is_unmatched(values: FillValues) -> None:
    assert one("Lieblingsfarbe", values) is None


def test_missing_value_is_unmatched() -> None:
    assert one("XING", FillValues(first_name="A")) is None
    assert one("Anrede", FillValues(), type="select", options=[("h", "Herr"), ("f", "Frau")]) is None
    assert one("Gehaltsvorstellung", FillValues()) is None


def test_maxlength_truncates_at_word_boundary(values: FillValues) -> None:
    v = FillValues(motivation="Das ist ein langer Satz ohne Ende")
    a = one("Motivation", v, type="textarea", maxlength=15)
    assert a is not None and a.value == "Das ist ein"
    assert truncate_at_word("abcdefghij", 4) == "abcd"
    assert truncate_at_word("ab cd", 5) == "ab cd"
    assert truncate_at_word("ab cd ef", 5) == "ab cd"


def test_number_and_date_inputs(values: FillValues) -> None:
    a = one("Gehaltsvorstellung", values, type="number")
    assert a is not None and a.value == "65000"
    a = one("Eintrittstermin", values, type="date")
    assert a is not None and a.value == "2026-12-01"
    a = one("Eintrittstermin", FillValues(earliest_start="sofort"), type="date")
    assert a is None  # cannot be expressed as a date


def test_select_and_radio(values: FillValues) -> None:
    opts = [("", "Bitte wählen"), ("AT", "Austria"), ("DE", "Germany")]
    a = one("Land", values, type="select", options=opts)
    assert a is not None and (a.kind, a.value) == ("select", "DE")
    a = one("Country", values, type="select", options=[("1", "Deutschland (DE)"), ("2", "Österreich")])
    assert a is not None and a.value == "1"
    v = FillValues(salutation="Frau")
    a = one("Anrede", v, type="select", options=[("m", "Herr"), ("w", "Frau"), ("d", "Divers")])
    assert a is not None and a.value == "w"
    a = one("Salutation", v, type="radio", options=[("m", "Mr"), ("w", "Ms")])
    assert (a.kind, a.value) == ("check", "w")
    a = one("Deutschkenntnisse", values, type="select", options=[("a", "B2"), ("n", "Muttersprache")])
    assert a is not None and a.value == "n"
    assert one("Land", values, type="select", options=[("x", "Frankreich")]) is None


def test_gender_only_when_provided(values: FillValues) -> None:
    assert one("Geschlecht", values, type="select", options=[("m", "männlich"), ("w", "weiblich")]) is None
    v = FillValues(gender="weiblich")
    a = one("Gender", v, type="select", options=[("m", "male"), ("w", "female")])
    assert a is not None and a.value == "w"


def test_uploads(values: FillValues) -> None:
    cv = mk("Lebenslauf", "file", field_id="a")
    cl = mk("Anschreiben", "file", field_id="b")
    z = mk("Zeugnisse", "file", field_id="c", multiple=True)
    z1 = mk("Weitere Dokumente", "file", field_id="d")
    values = dataclasses.replace(values, other_documents=[values.bundle_pdf])
    res = map_fields([cv, cl, z, z1], values)
    by = {a.field_id: a for a in res.assignments}
    assert by["a"].files == [values.cv] and by["a"].kind == "upload"
    assert by["b"].files == [values.cover_letter_pdf]
    assert by["c"].files == values.certificates
    assert by["d"].files == [values.bundle_pdf]
    assert not res.unmatched


@pytest.mark.parametrize("label", ["CV", "Resume", "Curriculum Vitae *", "Lebenslauf (PDF)"])
def test_cv_variants(label: str, values: FillValues) -> None:
    a = one(label, values, type="file")
    assert a is not None and a.files == [values.cv]


@pytest.mark.parametrize("label", ["Cover letter", "Motivationsschreiben", "Anschreiben (optional)"])
def test_cover_letter_variants(label: str, values: FillValues) -> None:
    a = one(label, values, type="file")
    assert a is not None and a.files == [values.cover_letter_pdf]


@pytest.mark.parametrize(
    "label", ["Zertifikate", "Other documents", "Anlagen", "Attachments", "Certificates", "Zeugnisse"]
)
def test_certificate_variants(label: str, values: FillValues) -> None:
    f = mk(label, "file", multiple=True)
    res = map_fields([f], values)
    assert res.assignments[0].files == values.certificates


def test_cover_letter_textarea_gets_motivation(values: FillValues) -> None:
    a = one("Anschreiben", values, type="textarea")
    assert a is not None and a.kind == "fill" and a.value == values.motivation


@pytest.mark.parametrize("label", ["Unterlagen", "Bewerbungsunterlagen (PDF)", "Upload", "Documents"])
def test_single_generic_upload_gets_bundle(label: str, values: FillValues) -> None:
    a = one(label, values, type="file")
    assert a is not None and a.files == [values.bundle_pdf]


def test_generic_upload_not_used_with_several_file_inputs(values: FillValues) -> None:
    res = map_fields([mk("Unterlagen", "file", field_id="a"), mk("Lebenslauf", "file", field_id="b")], values)
    assert [a.field_id for a in res.assignments] == ["b"]
    assert [f.field_id for f in res.unmatched] == ["a"]


def test_standard_answers_fallback() -> None:
    v = FillValues(standard_answers={"führerschein": "Klasse B", "Notice period": "ignored by rule"})
    a = one("Führerschein vorhanden?", v)
    assert a is not None and a.value == "Klasse B"


def test_checkbox_never_mapped(values: FillValues) -> None:
    res = map_fields([mk("Ich habe die Datenschutzerklärung gelesen", "checkbox")], values)
    assert not res.assignments and len(res.unmatched) == 1


def test_work_references_and_other_documents(values: FillValues) -> None:
    ref = values.cv
    values = dataclasses.replace(values, work_references=[ref], other_documents=[values.bundle_pdf])
    zeugnis = one("Arbeitszeugnis", values, type="file")
    other = one("Andere (Optional)", values, type="file")
    assert zeugnis is not None and zeugnis.files == [ref]
    assert other is not None and other.files == [values.bundle_pdf]


@pytest.mark.parametrize(("label", "attr"), [("Upload Urkunde", "certificates"), ("Diplom", "certificates"),
                                             ("Upload Arbeitsprobe", "work_samples"), ("Portfolio", "work_samples")])
def test_diplomas_and_work_samples(label: str, attr: str, values: FillValues) -> None:
    values = dataclasses.replace(values, work_samples=[values.cv])
    a = one(label, values, type="file", multiple=True)
    assert a is not None and a.files == getattr(values, attr)
