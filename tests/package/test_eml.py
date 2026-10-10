from __future__ import annotations

import email
from email import policy

from autoapp.package.eml import build_application_email, write_eml


def test_eml_roundtrip(tmp_path):
    pdf = tmp_path / "Bewerbungsmappe.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    msg = build_application_email(
        sender_name="Max Müller",
        sender_email="max@example.de",
        to="jobs@acme.de",
        subject="Bewerbung als Softwareentwickler, Kennziffer 2026-17 für Köln",
        body="Sehr geehrte Damen und Herren,\n\nanbei meine Unterlagen. Grüße aus Köln.\n",
        attachment=pdf,
    )
    path = tmp_path / "email.eml"
    write_eml(msg, path)

    parsed = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    assert parsed["To"] == "jobs@acme.de"
    assert parsed["Subject"] == "Bewerbung als Softwareentwickler, Kennziffer 2026-17 für Köln"
    assert parsed["From"].addresses[0].display_name == "Max Müller"
    assert parsed["From"].addresses[0].addr_spec == "max@example.de"
    assert parsed["Date"] is not None
    assert parsed["Message-ID"].endswith("@example.de>")
    body = parsed.get_body(preferencelist=("plain",))
    assert "Grüße aus Köln." in body.get_content()
    assert body.get_content_charset() == "utf-8"
    atts = list(parsed.iter_attachments())
    assert len(atts) == 1
    assert atts[0].get_filename() == "Bewerbungsmappe.pdf"
    assert atts[0].get_content_type() == "application/pdf"
    assert atts[0].get_content() == b"%PDF-1.4 fake"


def test_given_message_id_and_filename(tmp_path):
    pdf = tmp_path / "Mappe.pdf"
    pdf.write_bytes(b"x")
    msg = build_application_email(
        sender_name="A",
        sender_email="a@b.de",
        to="c@d.de",
        subject="s",
        body="b",
        attachment=pdf,
        message_id="<fixed@b.de>",
    )
    assert msg["Message-ID"] == "<fixed@b.de>"
    assert next(msg.iter_attachments()).get_filename() == "Mappe.pdf"
