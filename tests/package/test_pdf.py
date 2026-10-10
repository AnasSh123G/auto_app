from __future__ import annotations

import pytest
from pypdf import PdfReader

from autoapp.config.models import Personal
from autoapp.package.pdf import PdfRenderer, bundle, merge_pdfs
from autoapp.package.render import render_cover_letter_html
from autoapp.writing.schema import CoverLetter

PERSONAL = Personal(
    first_name="Max", last_name="Mustermann", email="max@example.de", phone="+49 170 1234567",
    street="Hauptstraße 1", postal_code="10115", city="Berlin",
)  # fmt: skip

SENTENCE = (
    "Ich entwickle seit mehreren Jahren Backend-Systeme in Python und habe dabei Verantwortung "
    "für Architektur, Tests und den Betrieb im Team übernommen. "
)


def letter_with_words(n: int) -> CoverLetter:
    words = (SENTENCE * (n // 25 + 2)).split()[:n]
    per = n // 4
    paras = [" ".join(words[i * per : (i + 1) * per if i < 3 else n]) + "." for i in range(4)]
    return CoverLetter(
        language="de",
        subject="Bewerbung als Softwareentwickler, Kennziffer 2026-17",
        salutation="Sehr geehrte Damen und Herren,",
        paragraphs=paras,
        closing="Mit freundlichen Grüßen",
        signature="Max Mustermann",
        motivation_short="",
        email_body="",
    )


def html_for(n: int) -> str:
    return render_cover_letter_html(
        letter_with_words(n),
        PERSONAL,
        ["Beispiel Software GmbH", "Personalabteilung", "Musterweg 12", "80331 München"],
        "Berlin, 7. Oktober 2026",
    )


@pytest.fixture(scope="module")
def renderer():
    with PdfRenderer() as r:
        yield r


def make_pdf(renderer: PdfRenderer, tmp_path, name: str, text: str):
    out = tmp_path / name
    renderer.html_to_pdf(f"<html><body><p>{text}</p></body></html>", out)
    return out


@pytest.mark.browser
def test_280_word_letter_is_one_page(renderer, tmp_path):
    out = tmp_path / "letter.pdf"
    assert renderer.html_to_pdf(html_for(280), out) == 1
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "Sehr geehrte Damen und Herren," in text
    assert "Berlin, 7. Oktober 2026" in text
    assert "Mustermann" in text


@pytest.mark.browser
def test_300_word_letter_is_one_page(renderer, tmp_path):
    assert renderer.html_to_pdf(html_for(300), tmp_path / "letter.pdf") == 1


@pytest.mark.browser
def test_700_word_letter_is_more_than_one_page(renderer, tmp_path):
    assert renderer.html_to_pdf(html_for(700), tmp_path / "letter.pdf") > 1


@pytest.mark.browser
def test_merge_order_and_bundle(renderer, tmp_path):
    letter = make_pdf(renderer, tmp_path, "letter.pdf", "LETTERTEXT")
    cv = make_pdf(renderer, tmp_path, "cv.pdf", "CVTEXT")
    c1 = make_pdf(renderer, tmp_path, "c1.pdf", "CERTONE")
    c2 = make_pdf(renderer, tmp_path, "c2.pdf", "CERTTWO")
    out = tmp_path / "Bewerbungsmappe.pdf"
    res = bundle(letter, cv, [c1, c2], out, max_mb=5)
    assert res.pages == 4 and not res.too_large and res.skipped == []
    assert res.size_bytes == out.stat().st_size
    texts = [p.extract_text() for p in PdfReader(str(out)).pages]
    for text, expected in zip(texts, ["LETTERTEXT", "CVTEXT", "CERTONE", "CERTTWO"], strict=True):
        assert expected in text


@pytest.mark.browser
def test_size_warning(renderer, tmp_path):
    letter = make_pdf(renderer, tmp_path, "letter.pdf", "LETTER")
    res = bundle(letter, None, [], tmp_path / "out.pdf", max_mb=0.0001)
    assert res.too_large and res.pages == 1


@pytest.mark.browser
def test_docx_cv_skipped(renderer, tmp_path):
    letter = make_pdf(renderer, tmp_path, "letter.pdf", "LETTER")
    cv = tmp_path / "cv.docx"
    cv.write_bytes(b"PK not a pdf")
    cert = make_pdf(renderer, tmp_path, "cert.pdf", "CERT")
    res = bundle(letter, cv, [cert], tmp_path / "out.pdf", max_mb=5)
    assert res.pages == 2
    assert len(res.skipped) == 1 and "cv.docx" in res.skipped[0]


@pytest.mark.browser
def test_merge_pdfs_returns_page_count(renderer, tmp_path):
    a = make_pdf(renderer, tmp_path, "a.pdf", "AAA")
    b = make_pdf(renderer, tmp_path, "b.pdf", "BBB")
    assert merge_pdfs([b, a], tmp_path / "m.pdf") == 2
    assert "BBB" in PdfReader(str(tmp_path / "m.pdf")).pages[0].extract_text()


def test_renderer_requires_context_manager(tmp_path):
    with pytest.raises(RuntimeError):
        PdfRenderer().html_to_pdf("<p>x</p>", tmp_path / "x.pdf")
