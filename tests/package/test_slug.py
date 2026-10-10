from __future__ import annotations

from autoapp.package.slug import folder_name, slugify


def test_golden_folder_name():
    assert (
        folder_name("Müller & Söhne GmbH", "Software-Entwickler (m/w/d)", "a1b2c3")
        == "application_mueller-soehne-gmbh_software-entwickler_a1b2c3"
    )


def test_transliteration_and_capitals():
    assert slugify("Äpfel Öl Über Straße") == "aepfel-oel-ueber-strasse"
    assert slugify("Café Zoë") == "cafe-zoe"


def test_gender_tags_removed():
    for tag in ("(m/w/d)", "(w/m/d)", "(d/w/m)", "(m/f/d)", "(all genders)", "(m/w/x)", "[m/w/d]", "m/w/d"):
        assert slugify(f"Data Engineer {tag}") == "data-engineer", tag
    assert slugify("Data Engineer (m/w/d) Berlin") == "data-engineer-berlin"


def test_punctuation_collapsed_and_no_edge_hyphens():
    assert slugify("  --A & B, Co.!! ") == "a-b-co"
    assert slugify("") == ""


def test_slug_max_len_no_trailing_hyphen():
    s = slugify("abcd efgh ijkl", 5)
    assert s == "abcd"
    assert len(slugify("x" * 100, 10)) == 10


def test_long_names_stay_under_100():
    name = folder_name("Sehr Lange Firma " * 20, "Senior Softwareentwickler " * 20, "a1b2c3")
    assert len(name) < 100
    assert name.startswith("application_") and name.endswith("_a1b2c3")
    comp, title = name.split("_")[1:3]
    assert comp and title and not comp.endswith("-") and not title.endswith("-")


def test_long_company_short_title_keeps_title():
    name = folder_name("Firma " * 40, "Dev", "abc123")
    assert len(name) < 100
    assert "_dev_" in name


def test_long_title_short_company_keeps_company():
    name = folder_name("ACME", "Titel " * 40, "abc123")
    assert len(name) < 100
    assert name.startswith("application_acme_")


def test_empty_names_fallback():
    assert folder_name("", "!!!", "id1") == "application_unknown_unknown_id1"
