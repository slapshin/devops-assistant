"""Email delivery: SMTP (stdlib ``smtplib`` in a thread) or a local outbox for tests and demos."""

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from itertools import count
from pathlib import Path
from typing import Protocol

from app.notifications.drivers.email.render import RenderedEmail
from app.settings import EmailProvider, Settings, SmtpSecurity

SMTP_TIMEOUT_SECONDS = 20.0


class EmailNotConfigured(Exception):
    pass


class EmailTransport(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


def build_message(sender: str, recipients: list[str], email: RenderedEmail) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = email.subject
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    return message


class SmtpTransport:
    def __init__(self, settings: Settings) -> None:
        assert settings.smtp_host is not None
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.security = settings.smtp_security
        self.tls_verify = settings.smtp_tls_verify
        self.username = settings.smtp_username
        self.password = settings.smtp_password
        self.timeout = SMTP_TIMEOUT_SECONDS

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if not self.tls_verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _send(self, message: EmailMessage) -> None:
        smtp: smtplib.SMTP
        if self.security is SmtpSecurity.TLS:
            smtp = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=self._context()
            )
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        with smtp:
            if self.security is SmtpSecurity.STARTTLS:
                smtp.starttls(context=self._context())
            if self.username is not None and self.password is not None:
                smtp.login(self.username, self.password.get_secret_value())
            smtp.send_message(message)


class OutboxTransport:
    """Writes each message to ``directory`` as ``<UTC time>-<n>.eml``; never sends anything."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._counter = count(1)

    async def send(self, message: EmailMessage) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.directory / f"{stamp}-{next(self._counter)}.eml"
        await asyncio.to_thread(self._write, path, message.as_bytes())

    def _write(self, path: Path, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def transport_from_settings(settings: Settings) -> EmailTransport | None:
    """None when email is not configured."""
    if settings.email_provider is EmailProvider.FAKE:
        return OutboxTransport(settings.outbox_dir)
    if settings.email_configured:
        return SmtpTransport(settings)
    return None


def sender_address(settings: Settings) -> str:
    return settings.smtp_from or "devops-assistant@localhost.localdomain"
