from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings


class AccountEmailSender(Protocol):
    def send_verification(self, recipient: str, display_name: str, url: str) -> None: ...

    def send_password_reset(self, recipient: str, display_name: str, url: str) -> None: ...

    def send_invitation(self, recipient: str, workspace_name: str, url: str) -> None: ...


class DisabledEmailSender:
    def send_verification(self, recipient: str, display_name: str, url: str) -> None:
        del recipient, display_name, url
        raise RuntimeError("Account email delivery is disabled")

    def send_password_reset(self, recipient: str, display_name: str, url: str) -> None:
        del recipient, display_name, url
        raise RuntimeError("Account email delivery is disabled")

    def send_invitation(self, recipient: str, workspace_name: str, url: str) -> None:
        del recipient, workspace_name, url
        raise RuntimeError("Account email delivery is disabled")


class SMTPEmailSender:
    """Minimal SMTP boundary. Credentials stay server-side and message bodies are never logged."""

    def __init__(self, settings: Settings):
        if not settings.smtp_host or not settings.smtp_from_address:
            raise RuntimeError("SMTP host and sender address are required")
        if any(character in settings.smtp_from_address for character in "\r\n"):
            raise RuntimeError("SMTP sender address contains invalid characters")
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.username = settings.smtp_username
        self.password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self.from_address = settings.smtp_from_address
        self.starttls = settings.smtp_starttls

    def _send(self, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as client:
            client.ehlo()
            if self.starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self.username and self.password:
                client.login(self.username, self.password)
            client.send_message(message)

    def send_verification(self, recipient: str, display_name: str, url: str) -> None:
        self._send(
            recipient,
            "Verify your EvidenceDesk email",
            (
                f"Hello {display_name},\n\n"
                "Confirm your email address to activate your private EvidenceDesk workspace:\n"
                f"{url}\n\n"
                "If you did not request this account, you can ignore this message."
            ),
        )

    def send_password_reset(self, recipient: str, display_name: str, url: str) -> None:
        self._send(
            recipient,
            "Reset your EvidenceDesk password",
            (
                f"Hello {display_name},\n\n"
                "Use this one-time link to choose a new EvidenceDesk password:\n"
                f"{url}\n\n"
                "If you did not request a reset, you can ignore this message."
            ),
        )

    def send_invitation(self, recipient: str, workspace_name: str, url: str) -> None:
        self._send(
            recipient,
            f"You are invited to {workspace_name} on EvidenceDesk",
            (
                f"You have been invited to the {workspace_name} EvidenceDesk workspace.\n\n"
                "Use this one-time link to accept the invitation:\n"
                f"{url}\n\n"
                "If you were not expecting this invitation, you can ignore this message."
            ),
        )


def build_account_email_sender(settings: Settings) -> AccountEmailSender:
    if settings.email_delivery_backend == "smtp":
        return SMTPEmailSender(settings)
    return DisabledEmailSender()
