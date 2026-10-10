import email
import hashlib
import socket
import ssl
import subprocess
import threading
from email import policy
from email.message import EmailMessage

import pytest
from aiosmtpd.controller import Controller

from autoapp.config.models import MailAccount
from autoapp.mail.connection import (
    MailSendError,
    SmtpMailer,
    imap_endpoint,
    pinned_context,
    saves_sent_copy_itself,
    smtp_endpoint,
)


def _msg() -> EmailMessage:
    m = EmailMessage()
    m["From"] = "Max Mustermann <max@example.com>"
    m["To"] = "jobs@muster-gmbh.de"
    m["Subject"] = "Bewerbung als Entwickler, Kennziffer 2026-17"
    m.set_content("Guten Tag,\n\nanbei meine Bewerbung.\n")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="Bewerbungsmappe.pdf")
    return m


def test_presets():
    gmail = MailAccount(preset="gmail", address="max@gmail.com")
    assert (smtp_endpoint(gmail).host, smtp_endpoint(gmail).port, smtp_endpoint(gmail).tls) == ("smtp.gmail.com", 465, "ssl")
    assert imap_endpoint(gmail).host == "imap.gmail.com"
    bridge = MailAccount(preset="proton_bridge", address="max@proton.me", bridge_cert_sha256="ab")
    assert (smtp_endpoint(bridge).host, smtp_endpoint(bridge).port, smtp_endpoint(bridge).tls) == ("127.0.0.1", 1025, "starttls")
    assert saves_sent_copy_itself(gmail) and saves_sent_copy_itself(bridge)
    assert not saves_sent_copy_itself(MailAccount(preset="custom", address="m@x.de", smtp_host="smtp.x.de"))


def test_bridge_must_be_local():
    acc = MailAccount(preset="proton_bridge", address="max@proton.me", smtp_host="mail.example.com")
    with pytest.raises(MailSendError):
        smtp_endpoint(acc)


class _Handler:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_send_through_local_smtp_server_and_append_only_for_custom(monkeypatch):
    handler = _Handler()
    port = _free_port()
    ctrl = Controller(handler, hostname="127.0.0.1", port=port, auth_require_tls=False,
                      authenticator=lambda *a: __import__("aiosmtpd.smtp", fromlist=["AuthResult"]).AuthResult(success=True))
    ctrl.start()
    appended = []

    class FakeImap:
        def __init__(self, host, port, timeout=None):
            pass

        def login(self, u, p):
            pass

        def append(self, folder, flags, date, data):
            appended.append((folder, data))

        def logout(self):
            pass

    monkeypatch.setattr("imaplib.IMAP4", FakeImap)
    try:
        acc = MailAccount(preset="custom", address="max@example.com", smtp_host="127.0.0.1", smtp_port=port,
                          smtp_tls="none", imap_host="127.0.0.1", imap_port=1, imap_tls="none", sent_folder="Gesendet")
        SmtpMailer(acc, "secret").send(_msg())
    finally:
        ctrl.stop()
    assert len(handler.messages) == 1
    received = email.message_from_bytes(handler.messages[0].content, policy=policy.default)
    assert received["Subject"].endswith("2026-17")
    assert [p.get_filename() for p in received.iter_attachments()] == ["Bewerbungsmappe.pdf"]
    assert len(appended) == 1 and appended[0][0] == '"Gesendet"'


def _self_signed(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                    "-days", "1", "-subj", "/CN=127.0.0.1"], check=True, capture_output=True)
    return key, crt


@pytest.mark.parametrize("matching", [True, False])
def test_pinned_context_accepts_only_the_pinned_certificate(tmp_path, matching):
    key, crt = _self_signed(tmp_path)
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    fp = hashlib.sha256(der).hexdigest() if matching else "00" * 32
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(crt, key)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)
        except (ssl.SSLError, OSError):
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    raw = socket.create_connection(("127.0.0.1", port))
    ctx = pinned_context(fp)
    if matching:
        s = ctx.wrap_socket(raw)
        s.close()
    else:
        with pytest.raises(ssl.SSLError, match="does not match"):
            ctx.wrap_socket(raw)
    t.join(timeout=5)
    srv.close()
