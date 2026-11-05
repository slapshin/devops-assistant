"""What every channel kind's driver implements (T026).

A driver formats a ``ReportMessage`` for its medium and delivers it to a channel's destination
with the channel's secrets. Errors a user should see are raised as ``DeliveryError`` with a
message that is already safe to store and show: it never contains a secret. The notifier
reports any other exception by its type name only, because library messages may embed secrets
(a Telegram bot token is part of every request URL).
"""

from typing import Protocol

from app.domain.notifications import MAX_OUTCOME_REASON_CHARS, AnyDestination, ChannelKind
from app.notifications.message import ReportMessage

DELIVERY_TIMEOUT_SECONDS = 20.0
"""Upper bound for delivering to one channel, retries included."""


class DeliveryError(Exception):
    """Delivery failed; the message is safe to record and show (no secrets)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason[:MAX_OUTCOME_REASON_CHARS])
        self.reason = reason[:MAX_OUTCOME_REASON_CHARS]


class ChannelNotConfigured(DeliveryError):
    """The server cannot deliver to this kind of channel at all."""


def redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def error_reason(exc: Exception) -> str:
    """``TypeName: message`` for exceptions whose message holds no secrets (e.g. smtplib)."""
    return f"{type(exc).__name__}: {exc}".strip().rstrip(":")[:MAX_OUTCOME_REASON_CHARS]


class ChannelDriver(Protocol):
    kind: ChannelKind

    def unavailable(self) -> str | None:
        """Why the server cannot deliver to this kind of channel; None when it can."""
        ...

    async def deliver(
        self,
        destination: AnyDestination,
        secrets: dict[str, str],
        message: ReportMessage,
        *,
        silent: bool,
    ) -> int:
        """Deliver to the destination; return the recipients or chats reached.

        ``silent`` asks for a delivery without an alert where the medium supports it.
        Raises DeliveryError.
        """
        ...

    async def test(self, destination: AnyDestination, secrets: dict[str, str]) -> str:
        """Send a short test message; return what was sent ("sent to …").

        Raises DeliveryError (ChannelNotConfigured when unavailable).
        """
        ...
