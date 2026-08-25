from __future__ import annotations

import pytest

from app.config import Settings
from app.email_delivery import DisabledEmailSender, SMTPEmailSender


class FakeSMTP:
    instance = None

    def __init__(self, host: str, port: int, timeout: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_count = 0
        self.tls_started = False
        self.login_args = None
        self.message = None
        FakeSMTP.instance = self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def ehlo(self):
        self.ehlo_count += 1

    def starttls(self, context):
        assert context is not None
        self.tls_started = True

    def login(self, username: str, password: str):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message


def test_smtp_sender_uses_starttls_authentication_and_plain_text(monkeypatch):
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", FakeSMTP)
    sender = SMTPEmailSender(
        Settings(
            _env_file=None,
            email_delivery_backend="smtp",
            smtp_host="smtp.example.test",
            smtp_port=587,
            smtp_username="server-user",
            smtp_password="server-password",
            smtp_from_address="EvidenceDesk <accounts@example.test>",
        )
    )
    sender.send_verification(
        "owner@example.test",
        "Owner",
        "https://accounts.example.test/verify-email?token=one-time-token",
    )
    client = FakeSMTP.instance
    assert client.host == "smtp.example.test"
    assert client.port == 587
    assert client.timeout == 15
    assert client.ehlo_count == 2
    assert client.tls_started is True
    assert client.login_args == ("server-user", "server-password")
    assert client.message["To"] == "owner@example.test"
    assert client.message.get_content_type() == "text/plain"
    assert "one-time-token" in client.message.get_content()

    sender.send_invitation(
        "member@example.test",
        "Synthetic Team",
        "https://accounts.example.test/invitation?token=invite-once",
    )
    invitation = FakeSMTP.instance.message
    assert invitation["To"] == "member@example.test"
    assert "Synthetic Team" in invitation["Subject"]
    assert "invite-once" in invitation.get_content()


def test_disabled_sender_fails_without_exposing_message_data():
    sender = DisabledEmailSender()
    with pytest.raises(RuntimeError, match="disabled"):
        sender.send_password_reset(
            "owner@example.test", "Owner", "https://example.test/reset?token=secret"
        )


def test_smtp_sender_rejects_header_injection():
    with pytest.raises(RuntimeError, match="invalid characters"):
        SMTPEmailSender(
            Settings(
                _env_file=None,
                email_delivery_backend="smtp",
                smtp_host="smtp.example.test",
                smtp_from_address="accounts@example.test\nBcc: attacker@example.test",
            )
        )
