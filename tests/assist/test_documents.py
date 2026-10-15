from pathlib import Path
from types import SimpleNamespace

from autoapp.assist.session import _folder_files


def test_future_arbeitszeugnis_and_unmapped_documents_are_available_for_upload(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    folder = tmp_path / "application"
    source.mkdir()
    folder.mkdir()
    (source / "cv.pdf").write_bytes(b"cv")
    secondary_cv = source / "cv_en.pdf"
    secondary_cv.write_bytes(b"secondary cv")
    future_reference = source / "Arbeitszeugnis_future.pdf"
    future_reference.write_bytes(b"reference")
    extra = source / "newly-added.pdf"
    extra.write_bytes(b"other")
    profile = SimpleNamespace(documents=[SimpleNamespace(file="cv.pdf", role="cv"),
                                         SimpleNamespace(file="cv_en.pdf", role="cv")])
    documents = SimpleNamespace(directory=source)

    files = _folder_files(folder, profile, documents)

    assert files["cv"] == source / "cv.pdf"
    assert files["work_references"] == [future_reference]
    assert set(files["other_documents"]) == {future_reference, extra}


def test_german_application_ignores_english_docs_even_if_old_folder_contains_them(tmp_path):
    from autoapp.config.models import DocumentEntry

    source, folder = tmp_path / "documents", tmp_path / "application"
    source.mkdir()
    folder.mkdir()
    entries = [DocumentEntry(file="cv_en.pdf", role="cv", language="en"),
               DocumentEntry(file="cv_de.pdf", role="cv", language="de"),
               DocumentEntry(file="certificate_en.pdf", role="certificate", language="en"),
               DocumentEntry(file="certificate_de.pdf", role="certificate", language="de"),
               DocumentEntry(file="degree.pdf", role="certificate", language="any")]
    for entry in entries:
        (source / entry.file).write_bytes(b"document")
        (folder / entry.file).write_bytes(b"old copy")
    (source / "Arbeitszeugnis_new.pdf").write_bytes(b"new reference")
    (source / "Portfolio_en.pdf").write_bytes(b"English portfolio")
    profile = SimpleNamespace(documents=entries)
    files = _folder_files(folder, profile, SimpleNamespace(directory=source), language="de")
    assert files["cv"] == folder / "cv_de.pdf"
    assert set(files["certificates"]) == {folder / "certificate_de.pdf", folder / "degree.pdf"}
    assert not any("_en" in path.name for paths in files.values() if isinstance(paths, list) for path in paths)
