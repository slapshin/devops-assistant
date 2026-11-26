"""On-demand channel operations (T026, T027): test messages and Telegram chat discovery.

Each operation checks that the server can deliver to the kind, resolves the secret (a draft
one, or the one stored for a saved channel), calls the driver, and turns every failure into a
safe error: a driver's ``DeliveryError`` reason is secret-free by contract, any other
exception is reported by its type only, since library messages may embed tokens. Scheduled
delivery stays in ``ReportNotifier``; both share ``ChannelDrivers``.
"""

from collections.abc import Awaitable
from datetime import UTC, datetime

from pydantic import SecretStr

from app.domain.notifications import (
    BOT_TOKEN,
    ChannelKind,
    ChannelKindInfo,
    ChannelTestResult,
    DestinationInput,
    TelegramChatList,
)
from app.notifications.drivers import ChannelDrivers
from app.notifications.drivers.base import ChannelDriver, ChannelNotConfigured, DeliveryError
from app.notifications.drivers.telegram.client import TelegramApiError
from app.notifications.drivers.telegram.driver import TelegramDriver
from app.storage.channels import SqliteChannelRepository

WEBHOOK_CONFLICT = 409
"""Telegram's error code when ``getUpdates`` is refused because the bot has a webhook."""
TEST_FAILED = "Sending the test message failed"
TELEGRAM_FAILED = "Telegram request failed"


class OperationNotConfigured(Exception):
    """The server cannot deliver to ``kind``; ``reason`` says why."""

    def __init__(self, kind: ChannelKind, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


class OperationFailed(Exception):
    """The channel's service refused or failed; ``reason`` is safe to show (no secrets)."""

    def __init__(self, title: str, reason: str) -> None:
        super().__init__(reason)
        self.title = title
        self.reason = reason


class TelegramWebhookSet(Exception):
    """The bot has a webhook, so Telegram does not list its recent chats."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TokenRequired(Exception):
    """Neither a bot token nor a channel with a stored one was given."""


class ChannelOperations:
    """Test messages and Telegram chat discovery for saved or draft channels.

    Raises OperationNotConfigured, OperationFailed, TelegramWebhookSet and TokenRequired, and
    the storage errors of secret resolution (ChannelNotFound, ChannelSecretRequired,
    SecretsUnreadable) unchanged.
    """

    def __init__(self, drivers: ChannelDrivers, channels: SqliteChannelRepository) -> None:
        self.drivers = drivers
        self.channels = channels

    def kinds(self) -> list[ChannelKindInfo]:
        """Every channel kind and whether this server can deliver to it."""
        statuses = {kind: self.drivers.status(kind) for kind in ChannelKind}
        return [
            ChannelKindInfo(kind=kind, available=reason is None, reason=reason)
            for kind, reason in statuses.items()
        ]

    async def send_test(
        self, destination: DestinationInput, channel_id: str | None
    ) -> ChannelTestResult:
        """Send a short test message; a secret the destination omits comes from ``channel_id``."""
        kind = destination.kind
        driver = self._available(kind)
        stored, secrets = await self.channels.secrets_for(destination, channel_id)
        detail = await _safely(kind, TEST_FAILED, driver.test(stored, secrets))
        return ChannelTestResult(kind=kind, detail=detail, sent_at=datetime.now(UTC))

    async def telegram_chats(
        self, bot_token: SecretStr | None, channel_id: str | None
    ) -> TelegramChatList:
        """The bot (its token, or the one stored for ``channel_id``) and its recent chats."""
        kind = ChannelKind.TELEGRAM
        driver = self._available(kind)
        if not isinstance(driver, TelegramDriver):
            raise OperationNotConfigured(kind, "This server's Telegram driver cannot list chats.")
        token = bot_token.get_secret_value().strip() if bot_token else ""
        secrets = {BOT_TOKEN: token} if token else {}
        if not secrets and channel_id is not None:
            _, secrets = await self.channels.resolve(channel_id)
        if not secrets.get(BOT_TOKEN):
            raise TokenRequired
        return await _safely(kind, TELEGRAM_FAILED, driver.chats(secrets), webhook_conflict=True)

    def _available(self, kind: ChannelKind) -> ChannelDriver:
        try:
            return self.drivers.available(kind)
        except ChannelNotConfigured as exc:
            raise OperationNotConfigured(kind, exc.reason) from None


async def _safely[T](
    kind: ChannelKind, title: str, call: Awaitable[T], *, webhook_conflict: bool = False
) -> T:
    """Await a driver call, turning every failure into a safe operation error."""
    try:
        return await call
    except ChannelNotConfigured as exc:
        raise OperationNotConfigured(kind, exc.reason) from None
    except TelegramApiError as exc:
        if webhook_conflict and exc.error_code == WEBHOOK_CONFLICT:
            raise TelegramWebhookSet(exc.reason) from None
        raise OperationFailed(title, exc.reason) from None
    except DeliveryError as exc:
        raise OperationFailed(title, exc.reason) from None
    except Exception as exc:  # never echo library messages: they may hold tokens
        raise OperationFailed(title, f"Unexpected error: {type(exc).__name__}") from None
