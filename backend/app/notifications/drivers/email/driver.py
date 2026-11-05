"""Email driver (T025 delivery behind the T026 driver interface)."""

from app.domain.notifications import AnyDestination, ChannelKind, EmailDestination
from app.notifications.drivers.base import ChannelNotConfigured, DeliveryError, error_reason
from app.notifications.drivers.email.render import RenderedEmail, render_email
from app.notifications.drivers.email.transport import EmailTransport, build_message
from app.notifications.message import APP_NAME, ReportMessage

NOT_CONFIGURED = "Email delivery is not configured (SMTP_HOST and SMTP_FROM)."
TEST_SUBJECT = f"[{APP_NAME}] Test email"
TEST_TEXT = (
    f"This is a test message from {APP_NAME}.\n"
    "Scheduled reports sent to this notification channel will arrive the same way.\n"
)


class EmailDriver:
    kind = ChannelKind.EMAIL

    def __init__(self, transport: EmailTransport | None, sender: str) -> None:
        self.transport = transport
        self.sender = sender

    def unavailable(self) -> str | None:
        return NOT_CONFIGURED if self.transport is None else None

    async def _send(self, recipients: list[str], email: RenderedEmail) -> None:
        if self.transport is None:
            raise ChannelNotConfigured(NOT_CONFIGURED)
        try:
            await self.transport.send(build_message(self.sender, recipients, email))
        except Exception as exc:  # smtplib messages hold no secrets (never the password)
            raise DeliveryError(error_reason(exc)) from None

    async def deliver(
        self,
        destination: AnyDestination,
        secrets: dict[str, str],
        message: ReportMessage,
        *,
        silent: bool,
    ) -> int:
        recipients = _email(destination).recipients
        await self._send(recipients, render_email(message))
        return len(recipients)

    async def test(self, destination: AnyDestination, secrets: dict[str, str]) -> str:
        recipients = _email(destination).recipients
        email = RenderedEmail(subject=TEST_SUBJECT, text=TEST_TEXT, html=f"<p>{TEST_TEXT}</p>")
        await self._send(recipients, email)
        return f"sent to {', '.join(recipients)}"


def _email(destination: AnyDestination) -> EmailDestination:
    if not isinstance(destination, EmailDestination):  # pragma: no cover - registry routing
        raise TypeError(f"not an email destination: {destination.kind}")
    return destination
