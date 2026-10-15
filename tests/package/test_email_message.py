"""The application-email builder (gmail-drafts-without-local-files)."""

from __future__ import annotations

import pytest

from autoapp.package.email_message import BUNDLE_NAME, NoStoredEmail, application_email
from autoapp.package.eml import build_application_email, write_eml
from autoapp.steps.context import RunContext
from autoapp.writing.schema import CoverLetter
from tests.documents.conftest import make_docx
from tests.helpers import PROFILE, _pdf, make_workspace
from tests.steps.test_prepare_step import FakeRenderer, approved_job, factory

DOCX_PROFILE = PROFILE.replace(
    "  - file: Zeugnis_Muster.pdf\n    role: certificate",
    "  - file: Zeugnis_Muster.pdf\n    role: certificate\n"
    "  - file: Projektbericht.docx\n    role: other\n    language: de",
)


def _letter() -> CoverLetter:
    return CoverLetter(
        language="de", subject="Bewerbung als Backend Developer",
        salutation="Sehr geehrte Frau Weber,",
        paragraphs=["Bei der Muster GmbH entwickle ich Python-Services mit FastAPI.",
                    "Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr."],
        closing="Mit freundlichen Grüßen", signature="Max Mustermann",
        motivation_short="", email_body="Guten Tag Frau Weber,\n\nanbei meine Bewerbung.",
    )


def _stored_email_job(ctx) -> str:
    jid = approved_job(ctx)
    ctx.repo.upsert_application(
        jid, channel="email", contact_email="jobs@muster-gmbh.de", language="de",
        letter_md=_letter().to_markdown(), email_subject="Bewerbung als Backend Developer",
        email_body="Guten Tag,\n\nanbei meine Bewerbung.", message_id="<abc@example.com>",
    )
    return jid


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def test_built_from_the_stored_letter(ctx):
    jid = _stored_email_job(ctx)
    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))) as built:
        msg = built.message
        assert msg["To"] == "jobs@muster-gmbh.de"
        assert msg["Subject"] == "Bewerbung als Backend Developer"
        assert msg.get_body(("plain",)).get_content().strip() == "Guten Tag,\n\nanbei meine Bewerbung."
        assert msg["Message-ID"] == "<abc@example.com>"
        assert [p.get_filename() for p in msg.iter_attachments()] == [BUNDLE_NAME]


def test_message_id_override(ctx):
    jid = _stored_email_job(ctx)
    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1])),
                           message_id="<new@example.com>") as built:
        assert built.message["Message-ID"] == "<new@example.com>"


def test_temporary_directory_is_removed(ctx):
    jid = _stored_email_job(ctx)
    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))) as built:
        tmp = built.bundle.parent
        assert tmp.is_dir() and built.bundle.name == BUNDLE_NAME
    assert not tmp.exists()


def test_temporary_directory_is_removed_on_error(ctx):
    jid = _stored_email_job(ctx)
    with pytest.raises(RuntimeError):
        with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))) as built:
            tmp = built.bundle.parent
            raise RuntimeError("boom")
    assert not tmp.exists()


def test_writes_nothing_into_the_workspace(ctx):
    jid = _stored_email_job(ctx)
    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))):
        pass
    assert list(ctx.ws.applications_dir.iterdir()) == []


def test_non_pdf_document_is_attached_separately(tmp_path):
    ws = make_workspace(tmp_path, profile=DOCX_PROFILE)
    make_docx(ws.documents_dir / "Projektbericht.docx", ["Projektbericht"])
    ctx = RunContext.open(ws)
    jid = _stored_email_job(ctx)
    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))) as built:
        names = [p.get_filename() for p in built.message.iter_attachments()]
        assert names == [BUNDLE_NAME, "Projektbericht.docx"]
    ctx.close()


def test_no_stored_email_raises(ctx):
    jid = approved_job(ctx)
    with pytest.raises(NoStoredEmail, match="prepare --job"):
        with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))):
            pass


def test_legacy_folder_email_is_read_as_is(ctx):
    jid = approved_job(ctx)
    folder = ctx.ws.applications_dir / f"application_muster-gmbh_backend-developer_{jid}"
    folder.mkdir(parents=True)
    _pdf(folder / BUNDLE_NAME, "Anschreiben")
    original = build_application_email(
        sender_name="Max Mustermann", sender_email="max.mustermann@example.com",
        to="jobs@muster-gmbh.de", subject="Bewerbung als Backend Developer",
        body="anbei meine Bewerbung", attachment=folder / BUNDLE_NAME,
    )
    write_eml(original, folder / "email.eml")
    ctx.repo.upsert_application(jid, folder=str(folder), channel="email",
                                contact_email="jobs@muster-gmbh.de", language="de")

    with application_email(ctx, jid, renderer_factory=factory(FakeRenderer([1]))) as built:
        assert str(built.message["Message-ID"]) == str(original["Message-ID"])
        assert str(built.message["Subject"]) == "Bewerbung als Backend Developer"
        assert built.bundle == folder / BUNDLE_NAME
    with application_email(ctx, jid, message_id="<new@example.com>",
                           renderer_factory=factory(FakeRenderer([1]))) as built:
        assert str(built.message["Message-ID"]) == "<new@example.com>"
    assert folder.exists() and (folder / "email.eml").exists()
