from __future__ import annotations

from pathlib import Path

import pytest

from autoapp.config.models import DocumentEntry, Profile
from autoapp.config.workspace import Workspace
from autoapp.documents import DocumentError, extract_text, load_documents, propose_roles

from .conftest import make_blank_pdf, make_docx, make_text_pdf


def profile_with(*entries: DocumentEntry) -> Profile:
    return Profile.model_validate(
        {
            "personal": {
                "first_name": "Max",
                "last_name": "M",
                "email": "max@example.com",
                "phone": "1",
                "street": "s",
                "postal_code": "1",
                "city": "c",
            },
            "documents": [e.model_dump() for e in entries],
        }
    )


def test_extract_pdf_and_docx(tmp_path: Path) -> None:
    pdf = make_text_pdf(tmp_path / "a.pdf", "Python developer in Berlin")
    docx = make_docx(tmp_path / "b.docx", ["Senior engineer", "Likes Rust"])
    assert "Python developer in Berlin" in extract_text(pdf)
    assert extract_text(docx) == "Senior engineer\nLikes Rust"


def test_extract_unsupported_type(tmp_path: Path) -> None:
    f = tmp_path / "x.png"
    f.write_bytes(b"\x89PNG")
    with pytest.raises(DocumentError, match="unsupported"):
        extract_text(f)


def test_missing_documents_folder(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    with pytest.raises(DocumentError) as ei:
        load_documents(ws, profile_with(DocumentEntry(file="cv.pdf", role="cv")))
    assert str(ws.documents_dir) in str(ei.value)


def test_requires_cv(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.documents_dir.mkdir()
    make_text_pdf(ws.documents_dir / "z.pdf", "Zeugnis")
    with pytest.raises(DocumentError, match="CV is required"):
        load_documents(ws, profile_with(DocumentEntry(file="z.pdf", role="certificate")))


def test_load_documents_extracts_cv_and_samples_and_lists_unmapped(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    d = ws.documents_dir
    d.mkdir()
    make_text_pdf(d / "Lebenslauf_de.pdf", "Mein Lebenslauf")
    make_docx(d / "Anschreiben.docx", ["Sehr geehrte Damen und Herren"])
    make_text_pdf(d / "Zeugnis.pdf", "Zeugnis")
    (d / "notes.txt").write_text("stray")
    (d / ".hidden").write_text("x")
    ps = profile_with(
        DocumentEntry(file="Lebenslauf_de.pdf", role="cv", language="de"),
        DocumentEntry(file="Anschreiben.docx", role="writing_sample"),
        DocumentEntry(file="Zeugnis.pdf", role="certificate"),
    )
    before = sorted(p.name for p in d.iterdir())
    docs = load_documents(ws, ps)
    assert sorted(p.name for p in d.iterdir()) == before
    assert "Mein Lebenslauf" in docs.texts["Lebenslauf_de.pdf"]
    assert "Sehr geehrte" in docs.texts["Anschreiben.docx"]
    assert "Zeugnis.pdf" not in docs.texts
    assert docs.unmapped == ["notes.txt"]
    assert docs.cv.file == "Lebenslauf_de.pdf"
    assert "Lebenslauf" in docs.cv_text("en")  # falls back to first CV


def test_image_only_cv_errors(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.documents_dir.mkdir()
    make_blank_pdf(ws.documents_dir / "scan.pdf")
    with pytest.raises(DocumentError, match="text-based PDF or DOCX"):
        load_documents(ws, profile_with(DocumentEntry(file="scan.pdf", role="cv")))


def test_declared_file_missing(tmp_path: Path) -> None:
    ws = Workspace(root=tmp_path)
    ws.documents_dir.mkdir()
    make_text_pdf(ws.documents_dir / "cv.pdf", "cv")
    ps = profile_with(
        DocumentEntry(file="cv.pdf", role="cv"), DocumentEntry(file="gone.pdf", role="certificate")
    )
    with pytest.raises(DocumentError, match="gone.pdf"):
        load_documents(ws, ps)


@pytest.mark.parametrize(
    ("name", "role", "lang"),
    [
        ("Lebenslauf_de.pdf", "cv", "de"),
        ("CV-EN.docx", "cv", "en"),
        ("my_resume_english.pdf", "cv", "en"),
        ("Zeugnis_2023.pdf", "certificate", None),
        ("Arbeitszeugnis_Firma.pdf", "work_reference", None),
        ("Zertifikat_AWS.pdf", "certificate", None),
        ("reference_letter.pdf", "work_reference", None),
        ("Bewerbungsfoto.jpg", "photo", None),
        ("foto.png", "photo", None),
        ("Anschreiben_deutsch.docx", "writing_sample", "de"),
        ("Cover_Letter_en.pdf", "writing_sample", "en"),
        ("random.pdf", "other", None),
        ("Dedicated.pdf", "other", None),
        ("scv.pdf", "other", None),
    ],
)
def test_propose_roles(name: str, role: str, lang: str | None) -> None:
    [e] = propose_roles([name])
    assert (e.file, e.role, e.language) == (name, role, lang)
