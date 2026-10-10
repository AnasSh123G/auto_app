"""HTML to PDF with Playwright's Chromium, and merging with pypdf (design decision 10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, Playwright, sync_playwright
from pypdf import PdfReader, PdfWriter


class PdfRenderer:
    """Owns one headless Chromium for the duration of a ``with`` block."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> PdfRenderer:
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception:
            self._pw.stop()
            self._pw = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            if self._pw is not None:
                self._pw.stop()
            self._pw = None

    def html_to_pdf(self, html: str, out: Path) -> int:
        """Render ``html`` to an A4 PDF at ``out`` (margins from CSS ``@page``); return the page count."""
        if self._browser is None:
            raise RuntimeError("PdfRenderer must be used as a context manager")
        out.parent.mkdir(parents=True, exist_ok=True)
        page = self._browser.new_page()
        try:
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            page.close()
        return page_count(out)


def page_count(pdf: Path) -> int:
    return len(PdfReader(str(pdf)).pages)


def merge_pdfs(parts: list[Path], out: Path) -> int:
    """Concatenate ``parts`` in order into ``out``; return the total page count."""
    writer = PdfWriter()
    for part in parts:
        writer.append(str(part))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)
    writer.close()
    return page_count(out)


@dataclass
class BundleResult:
    pages: int
    size_bytes: int
    too_large: bool
    skipped: list[str] = field(default_factory=list)  # warnings for documents left out of the merge


def _is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def bundle(
    letter_pdf: Path,
    cv: Path | None,
    certificates: list[Path],
    out: Path,
    max_mb: float,
) -> BundleResult:
    """Merge letter, CV and certificates (in that order) into ``out``.

    A non-PDF CV or certificate (e.g. DOCX) cannot be merged; it is listed in ``skipped``
    and the caller still copies the file into the folder.
    """
    parts = [letter_pdf]
    skipped: list[str] = []
    for label, doc in [("CV", cv), *[("Certificate", c) for c in certificates]]:
        if doc is None:
            continue
        if _is_pdf(doc):
            parts.append(doc)
        else:
            skipped.append(
                f"{label} {doc.name} is not a PDF and was left out of Bewerbungsmappe.pdf; "
                "use a PDF so it can be merged."
            )
    pages = merge_pdfs(parts, out)
    size = out.stat().st_size
    return BundleResult(pages=pages, size_bytes=size, too_large=size > max_mb * 1024 * 1024, skipped=skipped)
