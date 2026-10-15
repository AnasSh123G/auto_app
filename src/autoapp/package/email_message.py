"""The complete application email, built in a temporary directory from the stored letter (gmail-drafts-without-local-files)."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING

from autoapp.evaluation.schema import FinalEvaluation
from autoapp.package import render as R
from autoapp.package.documents_select import select_documents
from autoapp.package.eml import build_application_email, load_message
from autoapp.package.pdf import PdfRenderer, bundle
from autoapp.writing.letter_format import date_line
from autoapp.writing.schema import CoverLetter

if TYPE_CHECKING:
    from autoapp.steps.context import RunContext

BUNDLE_NAME = "Bewerbungsmappe.pdf"


@dataclass
class BuiltEmail:
    """The message to send or draft, its bundle PDF and non-fatal warnings."""

    message: EmailMessage
    bundle: Path
    warnings: list[str] = field(default_factory=list)


class NoStoredEmail(ValueError):
    """The job has neither stored letter text nor an email.eml."""


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


@contextmanager
def application_email(
    ctx: RunContext,
    job_id: str,
    *,
    renderer_factory: Callable[[], AbstractContextManager[PdfRenderer]] = PdfRenderer,
    message_id: str | None = None,
) -> Iterator[BuiltEmail]:
    """The job's complete application email.

    An old folder with email.eml is read as it is (legacy). Otherwise the email is
    built from the stored letter in a TemporaryDirectory(prefix="autoapp-email-")
    outside the workspace, which is removed when the with-block ends, also on errors.
    ``message_id`` overrides the stored Message-ID (draft --again).
    """
    repo = ctx.repo
    app = repo.get_application(job_id)
    job = repo.get_job(job_id)
    folder = Path(app["folder"]) if app and app["folder"] else None
    if folder is not None and (folder / "email.eml").exists():
        msg = load_message(folder)
        if message_id is not None:
            del msg["Message-ID"]
            msg["Message-ID"] = message_id
        yield BuiltEmail(msg, folder / BUNDLE_NAME, [])
        return
    if app is None or job is None or not app["letter_md"]:
        raise NoStoredEmail(
            f"no stored email for job {job_id}; run `autoapp prepare --job {job_id} --force`"
        )

    with tempfile.TemporaryDirectory(prefix="autoapp-email-") as tmp_name:
        tmp = Path(tmp_name)
        language = app["language"] or "de"
        letter = parse_cover_letter_md(app["letter_md"], language)
        personal = ctx.profile.personal
        recipient = [job["company"]]
        if app["contact_person"]:
            recipient.append(("z. Hd. " if language == "de" else "Attn. ") + app["contact_person"])
        html = R.render_cover_letter_html(letter, personal, recipient,
                                          date_line(personal.city, date.today(), language))
        warnings: list[str] = []
        with renderer_factory() as renderer:
            pages = renderer.html_to_pdf(html, tmp / "cover_letter.pdf")
        if pages > 1:
            warnings.append(f"the cover letter is {pages} pages long")

        requested: list[str] = []
        ev_row = repo.current_evaluation(job_id)
        if ev_row is not None:
            final = FinalEvaluation.model_validate(json.loads(ev_row["payload"] or "{}"))
            requested = list(final.result.requested_documents)
        selection = select_documents(ctx.profile, language, requested,
                                     ctx.config.writing.always_attach_certificates)
        docs = ctx.documents
        cv_path = docs.path_of(selection.cv) if selection.cv else None
        cert_paths = [docs.path_of(entry) for entry in selection.certificates]
        b = bundle(tmp / "cover_letter.pdf", cv_path, cert_paths, tmp / BUNDLE_NAME,
                   ctx.config.email.max_bundle_mb)
        warnings += b.skipped
        extras = [docs.path_of(entry) for entry in selection.extras]
        if selection.photo:
            extras.append(docs.path_of(selection.photo))
        extras.extend(path for path in ([cv_path] if cv_path else []) + cert_paths
                      if path.suffix.lower() != ".pdf")
        msg = build_application_email(
            sender_name=f"{personal.first_name} {personal.last_name}", sender_email=personal.email,
            to=app["contact_email"], subject=app["email_subject"] or letter.subject,
            body=app["email_body"] or "", attachment=tmp / BUNDLE_NAME,
            extra_attachments=extras, message_id=message_id or app["message_id"],
        )
        yield BuiltEmail(msg, tmp / BUNDLE_NAME, warnings)
