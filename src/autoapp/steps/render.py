"""Render step: rebuild the PDFs and the email draft from an edited ``cover_letter.md``.

Makes no LLM calls and does not lint the user's own wording.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from autoapp.config.models import DocumentEntry
from autoapp.package import render as R
from autoapp.package.documents_select import document_matches_language, select_documents
from autoapp.package.eml import build_application_email, write_eml
from autoapp.package.pdf import PdfRenderer, bundle
from autoapp.steps.context import RunContext
from autoapp.steps.send import BUNDLE, load_message
from autoapp.writing.letter_format import date_line
from autoapp.writing.schema import CoverLetter


def parse_cover_letter_md(text: str, language: str) -> CoverLetter:
    """Inverse of ``CoverLetter.to_markdown``: subject, salutation, paragraphs, closing, signature."""
    lines = text.strip().splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("cover_letter.md must start with '# <subject>'")
    subject = lines[0][2:].strip()
    blocks = [b.strip() for b in "\n".join(lines[1:]).strip().split("\n\n") if b.strip()]
    if len(blocks) < 3:
        raise ValueError("cover_letter.md needs a salutation, at least one paragraph and a closing")
    salutation, *paragraphs, end = blocks
    end_lines = end.splitlines()
    if len(end_lines) >= 2:
        closing, signature = end_lines[0].strip(), "\n".join(end_lines[1:]).strip()
    else:  # closing and signature separated by a blank line
        closing, signature = paragraphs.pop().strip() if paragraphs else "", end.strip()
    return CoverLetter(language=language, subject=subject, salutation=salutation, paragraphs=paragraphs,
                       closing=closing, signature=signature, motivation_short="", email_body="")


def render_application(ctx: RunContext, job_id: str, *, renderer_factory=PdfRenderer,
                       today: date | None = None) -> list[str]:
    """Re-render one application folder; returns warnings."""
    repo = ctx.repo
    app = repo.get_application(job_id)
    job = repo.get_job(job_id)
    if app is None or job is None or not app["folder"]:
        raise ValueError(f"job {job_id} has not been prepared; run `autoapp prepare` first")
    folder = Path(app["folder"])
    language = app["language"] or "de"
    letter = parse_cover_letter_md((folder / "cover_letter.md").read_text(encoding="utf-8"), language)
    personal = ctx.profile.personal
    recipient = [job["company"]]
    if app["contact_person"]:
        recipient.append(("z. Hd. " if language == "de" else "Attn. ") + app["contact_person"])
    html = R.render_cover_letter_html(letter, personal, recipient,
                                      date_line(personal.city, today or date.today(), language))
    warnings: list[str] = []
    with renderer_factory() as renderer:
        pages = renderer.html_to_pdf(html, folder / "cover_letter.pdf")
    if pages > 1:
        warnings.append(f"the cover letter is {pages} pages long")
    docs = {p.name for p in folder.iterdir()}
    selection = select_documents(ctx.profile, language, [], ctx.config.writing.always_attach_certificates)
    cv = folder / selection.cv.file if selection.cv and selection.cv.file in docs else None
    certs = [folder / e.file for e in selection.certificates if e.file in docs]
    extras = [folder / e.file for e in selection.extras if e.file in docs]
    if selection.photo and selection.photo.file in docs:
        extras.append(folder / selection.photo.file)
    extras.extend(path for path in ([cv] if cv else []) + certs if path.suffix.lower() != ".pdf")
    b = bundle(folder / "cover_letter.pdf", cv, certs, folder / BUNDLE, ctx.config.email.max_bundle_mb)
    warnings += b.skipped
    if b.too_large:
        warnings.append(f"{BUNDLE} is larger than {ctx.config.email.max_bundle_mb} MB")
    if (folder / "email.eml").exists():
        old = load_message(folder)
        body = old.get_body(("plain",))
        bundled_names = {BUNDLE, *(path.name for path in ([cv] if cv else []) + certs
                                  if path.suffix.lower() == ".pdf")}
        for part in old.iter_attachments():
            filename = part.get_filename()
            if not filename or filename in bundled_names or Path(filename).name != filename:
                continue
            entry = next((entry for entry in ctx.profile.documents if Path(entry.file).name == filename), None)
            if entry is not None:
                if not document_matches_language(entry, language):
                    continue
                if entry.role == "cv" and (selection.cv is None or entry.file != selection.cv.file):
                    continue
            elif not document_matches_language(DocumentEntry(file=filename), language):
                continue
            copied = folder / filename
            if copied.is_file():
                extras.append(copied)
        unique_extras = {path.resolve(): path for path in extras}.values()
        attachments_size = b.size_bytes + sum(path.stat().st_size for path in unique_extras)
        if attachments_size > ctx.config.email.max_bundle_mb * 1024 * 1024:
            warnings.append(
                f"Combined email attachments total {attachments_size / 1024 / 1024:.1f} MB; "
                f"max_bundle_mb={ctx.config.email.max_bundle_mb:g} MB is checked against "
                "Bewerbungsmappe.pdf only."
            )
        msg = build_application_email(
            sender_name=f"{personal.first_name} {personal.last_name}", sender_email=personal.email,
            to=old["To"], subject=letter.subject, body=body.get_content() if body else "",
            attachment=folder / BUNDLE, extra_attachments=extras,
        )
        write_eml(msg, folder / "email.eml")
    return warnings
