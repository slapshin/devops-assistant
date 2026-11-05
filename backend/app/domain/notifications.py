"""Notification channels and scheduled-report notifications (T025, T026, T027).

A **notification channel** is configured once for the whole application: a name and a
destination of one kind (email recipients, a Telegram chat with its bot token). It says only
*where* a report goes. A project's ``ReportSchedule`` lists the channels its scheduled reports
are sent to, each with a policy saying *when* (after every run, or on problems at or above a
severity), so one channel can serve many projects with different rules.

When a scheduled analysis finishes, every enabled notification whose policy matches is
delivered, independently of the others. Delivery is a side effect: its outcomes are recorded on
the job (``AnalysisJob.notifications``) and never change the job's state. Manual analyses are
never delivered.

A channel's secret (the Telegram bot token) is write-only: input models accept it, read models
only say whether one is stored.
"""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator

from app.domain.common import Contract, Severity, UtcDatetime

MAX_SCHEDULE_NOTIFICATIONS = 10
MAX_CHANNEL_NAME_CHARS = 100
MAX_CHANNEL_DESCRIPTION_CHARS = 1000
MAX_RECIPIENTS = 20
MAX_EMAIL_CHARS = 254
MAX_OUTCOME_REASON_CHARS = 300
# Deliberately strict: one plain address per entry, nothing a mail header would interpret
# (display names, comments, lists, quoting, line breaks).
_EMAIL = re.compile(
    r"^[^@\s,;:<>()\[\]\"\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)
TELEGRAM_CHAT_ID_PATTERN = r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$"
# Loose on purpose: catches a pasted chat ID or URL, not every malformed token.
_TELEGRAM_TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

SEVERITY_ORDER = list(Severity)
"""Lowest first."""


def validate_recipients(value: list[str]) -> list[str]:
    """Strip, validate, and deduplicate (case-insensitively) email addresses, keeping order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        address = raw.strip()
        if len(address) > MAX_EMAIL_CHARS or not _EMAIL.match(address):
            raise ValueError(f"not a valid email address: {address!r}")
        if address.lower() not in seen:
            seen.add(address.lower())
            result.append(address)
    return result


class ChannelKind(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"


class NotifyWhen(StrEnum):
    ALWAYS = "always"
    """Every finished scheduled analysis, including ones without findings."""
    PROBLEMS = "problems"
    """A failed or partial analysis, or one with a finding at or above ``min_severity``."""


class NotificationPolicy(Contract):
    when: NotifyWhen = Field(
        default=NotifyWhen.PROBLEMS,
        description="always, or problems: failed or partial analyses and findings at or "
        "above min_severity.",
    )
    min_severity: Severity = Field(
        default=Severity.HIGH,
        description="Lowest finding severity that counts as a problem. With always, reports "
        "below it are delivered silently where the channel supports that (Telegram).",
    )


# --- destinations -----------------------------------------------------------------------------


class EmailDestination(Contract):
    """Email the report to ``recipients`` through the server's SMTP configuration."""

    kind: Literal[ChannelKind.EMAIL] = ChannelKind.EMAIL
    recipients: list[str] = Field(
        min_length=1,
        max_length=MAX_RECIPIENTS,
        description="Plain email addresses (no display names); duplicates are removed.",
        examples=[["oncall@example.com"]],
    )

    @field_validator("recipients")
    @classmethod
    def _valid_recipients(cls, value: list[str]) -> list[str]:
        return validate_recipients(value)


class _TelegramChat(Contract):
    chat_id: str = Field(
        pattern=TELEGRAM_CHAT_ID_PATTERN,
        description="Numeric chat ID (groups and channels are negative) or @channel_username.",
        examples=["-1001234567890"],
    )
    message_thread_id: int | None = Field(
        default=None, ge=1, description="Forum topic of a supergroup; null for the main chat."
    )

    @field_validator("chat_id", mode="before")
    @classmethod
    def _strip_chat_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TelegramDestinationInput(_TelegramChat):
    """Post the report into a Telegram chat with a bot."""

    kind: Literal[ChannelKind.TELEGRAM] = ChannelKind.TELEGRAM
    bot_token: SecretStr | None = Field(
        default=None,
        description="Bot token from @BotFather. Omit to keep the stored token (update only).",
    )

    @field_validator("bot_token")
    @classmethod
    def _valid_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        token = value.get_secret_value().strip()
        if not _TELEGRAM_TOKEN.match(token):
            raise ValueError("not a Telegram bot token (expected 123456:ABC-… from @BotFather)")
        return SecretStr(token)


class TelegramDestination(_TelegramChat):
    kind: Literal[ChannelKind.TELEGRAM] = ChannelKind.TELEGRAM
    bot_token_set: bool


DestinationInput = Annotated[
    EmailDestination | TelegramDestinationInput, Field(discriminator="kind")
]
Destination = Annotated[EmailDestination | TelegramDestination, Field(discriminator="kind")]
AnyDestination = EmailDestination | TelegramDestination


def destination_count(destination: AnyDestination) -> int:
    """Recipients a destination addresses: email addresses, or one chat."""
    if isinstance(destination, EmailDestination):
        return len(destination.recipients)
    return 1


# --- channels ---------------------------------------------------------------------------------


class NotificationChannelInput(Contract):
    """Body of POST /api/notification-channels and PUT /api/notification-channels/{id}."""

    name: str = Field(min_length=1, max_length=MAX_CHANNEL_NAME_CHARS, examples=["Ops chat"])
    description: str | None = Field(default=None, max_length=MAX_CHANNEL_DESCRIPTION_CHARS)
    destination: DestinationInput

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not (stripped := " ".join(value.split())):
            raise ValueError("must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class ChannelUsage(Contract):
    project_id: str
    name: str


class NotificationChannel(Contract):
    channel_id: str
    name: str
    description: str | None = None
    destination: Destination
    readable: bool = Field(
        default=True,
        description="False when the stored secret cannot be decrypted (lost or changed key).",
    )
    used_by: list[ChannelUsage] = Field(
        default_factory=list, description="Projects whose schedule sends to the channel."
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime


class NotificationChannelList(Contract):
    items: list[NotificationChannel]


# --- schedule notifications -------------------------------------------------------------------


class ScheduleNotification(Contract):
    """Send a project's scheduled reports to a channel when ``policy`` matches."""

    channel_id: str = Field(min_length=1, max_length=36)
    enabled: bool = True
    policy: NotificationPolicy = Field(default_factory=NotificationPolicy)


# --- outcomes ---------------------------------------------------------------------------------


class NotificationStatus(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"
    """The policy did not match (e.g. no finding at or above the minimum severity)."""
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    """The server cannot deliver to this kind of channel (e.g. no SMTP configured)."""


class DeliveryOutcome(Contract):
    """What happened to one notification when a scheduled analysis finished."""

    channel_id: str | None = Field(
        default=None, description="Null for outcomes recorded before channels existed (T025)."
    )
    name: str = Field(description="The channel's name at delivery time.")
    kind: ChannelKind
    status: NotificationStatus
    destinations: int = Field(ge=0, description="Recipients or chats addressed.")
    reason: str | None = Field(
        default=None,
        max_length=MAX_OUTCOME_REASON_CHARS,
        description="Why skipped, failed, or not configured.",
    )
    at: UtcDatetime


# --- API --------------------------------------------------------------------------------------


class ChannelKindInfo(Contract):
    kind: ChannelKind
    available: bool
    reason: str | None = Field(default=None, description="Why the server cannot deliver.")


class ChannelKindList(Contract):
    """GET /api/notification-channels/kinds."""

    items: list[ChannelKindInfo]


class ChannelTestRequest(Contract):
    """Body of POST /api/notification-channels/test: a destination, saved or not.

    ``channel_id`` supplies a stored secret the destination omits (an edited channel)."""

    channel_id: str | None = Field(default=None, max_length=36)
    destination: DestinationInput


class ChannelTestResult(Contract):
    kind: ChannelKind
    detail: str = Field(description='What was sent, e.g. "sent to Ops chat (supergroup)".')
    sent_at: UtcDatetime


class TelegramChatsRequest(Contract):
    """Body of POST /api/notification-channels/telegram/chats: a bot token, or the channel
    whose stored token to use."""

    channel_id: str | None = Field(default=None, max_length=36)
    bot_token: SecretStr | None = None


class TelegramChat(Contract):
    chat_id: str
    title: str
    type: str = Field(description="private, group, supergroup or channel.")
    username: str | None = None
    is_forum: bool = False


class TelegramChatList(Contract):
    """Chats that recently messaged the bot or added it (Telegram keeps updates ≤ 24 h)."""

    bot_username: str | None = Field(description="The bot, e.g. ops_alerts_bot.")
    items: list[TelegramChat]
