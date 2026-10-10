from __future__ import annotations

import pytest

from autoapp.discovery.normalize import (
    canonical_url,
    fingerprint,
    group_key,
    norm_company,
    norm_text,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://www.linkedin.com/comm/jobs/view/3912345678/?trackingId=abc&refId=x&lipi=y",
            "https://www.linkedin.com/jobs/view/3912345678/",
        ),
        (
            "https://de.linkedin.com/jobs/view/senior-data-scientist-at-foo-3912345678?trk=public&refId=1",
            "https://www.linkedin.com/jobs/view/3912345678/",
        ),
        ("https://www.linkedin.com/jobs/view/3912345678", "https://www.linkedin.com/jobs/view/3912345678/"),
        (
            "https://DE.Indeed.com/viewjob?jk=abc123&utm_source=mail&from=email&vjs=3#section",
            "https://de.indeed.com/viewjob?jk=abc123",
        ),
        (
            "https://www.stepstone.de/stellenangebote--Foo-13850318-inline.html/?wt_mc=x&cid=1&adjust_t=2",
            "https://www.stepstone.de/stellenangebote--Foo-13850318-inline.html",
        ),
        ("https://example.com/jobs?id=42&jobId=7&utm_campaign=z", "https://example.com/jobs?id=42&jobId=7"),
        ("https://example.com/job/12345/", "https://example.com/job/12345"),
        ("https://example.com/job/12345#apply", "https://example.com/job/12345"),
        ("https://example.com/", "https://example.com"),
    ],
)
def test_canonical_url(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


def test_canonical_url_is_idempotent_and_param_order_insensitive() -> None:
    a = canonical_url("https://example.com/j?b=2&a=1&utm_source=x")
    assert a == canonical_url("https://example.com/j?a=1&b=2")
    assert canonical_url(a) == a


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Data Scientist (m/w/d)", "data scientist"),
        ("Data Scientist (w/m/d)", "data scientist"),
        ("Data Scientist (d/w/m)", "data scientist"),
        ("Data Scientist (m/f/d)", "data scientist"),
        ("Data Scientist (all genders)", "data scientist"),
        ("Berater (gn)", "berater"),
        ("Pilot (a*)", "pilot"),
        ("Entwickler*in Backend", "entwickler backend"),
        ("Ingenieur:in", "ingenieur"),
        ("Softwareentwickler/in", "softwareentwickler"),
        ("Data Scientist m/w/d", "data scientist"),
        ("Büro-Kaufmann (m/w/d) in Köln", "buero kaufmann in koeln"),
        ("Straßenbauer   –  Süd!", "strassenbauer sued"),
        ("AI Engineer (d/w/m) - im Bereich Consulting", "ai engineer im bereich consulting"),
        ("Fachinformatiker in Berlin", "fachinformatiker in berlin"),
    ],
)
def test_norm_text(raw: str, expected: str) -> None:
    assert norm_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Müller & Söhne GmbH", "mueller soehne"),
        ("Forto Logistics SE & Co. KG", "forto logistics"),
        ("Deutsche Bahn AG", "deutsche bahn"),
        ("4flow SE", "4flow"),
        ("Siemens Energy Global GmbH & Co. KG", "siemens energy global"),
        ("Acme Inc.", "acme"),
        ("Acme Ltd", "acme"),
        ("Acme Limited", "acme"),
        ("Hilfswerk e.V.", "hilfswerk"),
        ("Foo mbH", "foo"),
        ("Seven Senders GmbH", "seven senders"),
        ("Sage", "sage"),
    ],
)
def test_norm_company(raw: str, expected: str) -> None:
    assert norm_company(raw) == expected


def test_fingerprint_ignores_gender_tag_variants() -> None:
    a = fingerprint("Acme GmbH", "Data Scientist (m/w/d)", "Berlin")
    b = fingerprint("ACME GmbH", "Data Scientist (w/m/d)", "berlin")
    c = fingerprint("Acme", "Data Scientist (m/f/d)", "Berlin")
    assert a == b == c
    assert len(a) == 40


def test_fingerprint_differs_by_city_title_company() -> None:
    base = fingerprint("Acme GmbH", "Data Scientist", "Berlin")
    assert base != fingerprint("Acme GmbH", "Data Scientist", "Hamburg")
    assert base != fingerprint("Acme GmbH", "Data Engineer", "Berlin")
    assert base != fingerprint("Other GmbH", "Data Scientist", "Berlin")


def test_umlaut_forms_match() -> None:
    assert fingerprint("Müller AG", "Entwickler", "Köln") == fingerprint("Mueller", "Entwickler", "Koeln")


def test_multi_city_group_key_same_fingerprint_differs() -> None:
    title = "AI Engineer (d/w/m) - im Bereich Consulting"
    assert group_key("4flow", title) == group_key("4flow SE", "AI Engineer (m/w/d) - im Bereich Consulting")
    assert fingerprint("4flow", title, "Düsseldorf") != fingerprint("4flow", title, "Rüsselsheim")
