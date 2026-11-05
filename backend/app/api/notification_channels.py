"""Notification channel routes (T026, T027): CRUD, kinds the server can deliver to, test
messages, and Telegram chat lookup. Secrets are write-only and never echoed in problems."""

from datetime import UTC, datetime

from fastapi import APIRouter, Response

from app.api.problems import ProblemError, problem_responses, validation_problem
from app.api.routes import ServicesDep
from app.container import Services
from app.domain.jobs import ErrorCode
from app.domain.notifications import (
    ChannelKind,
    ChannelKindInfo,
    ChannelKindList,
    ChannelTestRequest,
    ChannelTestResult,
    NotificationChannel,
    NotificationChannelInput,
    NotificationChannelList,
    TelegramChatList,
    TelegramChatsRequest,
)
from app.notifications.drivers.base import ChannelDriver, ChannelNotConfigured, DeliveryError
from app.notifications.drivers.telegram.client import TelegramApiError
from app.notifications.drivers.telegram.driver import BOT_TOKEN, TelegramDriver
from app.storage.channels import (
    ChannelInUse,
    ChannelNameTaken,
    ChannelNotFound,
    ChannelSecretRequired,
)
from app.storage.secrets import SecretsUnreadable

router = APIRouter(prefix="/api/notification-channels", tags=["notification channels"])

WEBHOOK_CONFLICT = 409


def _not_found(channel_id: str) -> ProblemError:
    return ProblemError(
        404,
        ErrorCode.CHANNEL_NOT_FOUND,
        "Notification channel not found",
        f"No channel {channel_id}.",
    )


def _unreadable() -> ProblemError:
    return ProblemError(
        409,
        ErrorCode.CREDENTIALS_UNREADABLE,
        "Stored secret is unreadable",
        "The secret key changed or was lost; re-enter the channel's secret.",
    )


def _not_configured(kind: ChannelKind, reason: str) -> ProblemError:
    return ProblemError(
        409,
        ErrorCode.CHANNEL_NOT_CONFIGURED,
        f"{kind.value.capitalize()} is not configured",
        f"{reason} Change the server configuration and restart the app.",
    )


def _failed(title: str, reason: str) -> ProblemError:
    return ProblemError(502, ErrorCode.DELIVERY_FAILED, title, reason)


def _write_error(exc: Exception) -> ProblemError:
    match exc:
        case ChannelNameTaken(name=name):
            return ProblemError(
                409,
                ErrorCode.CHANNEL_NAME_TAKEN,
                "Channel name taken",
                f"A notification channel named {name!r} already exists.",
            )
        case ChannelSecretRequired(field=field):
            return validation_problem(f"body.{field}", "required when no secret is stored")
        case SecretsUnreadable():
            return _unreadable()
        case ChannelNotFound():
            return validation_problem("body.channel_id", "no such notification channel")
    raise exc


def _driver(services: Services, kind: ChannelKind) -> ChannelDriver:
    driver = services.notifier.drivers.get(kind)
    if driver is None:
        raise _not_configured(kind, f"No {kind} driver on this server.")
    if (reason := driver.unavailable()) is not None:
        raise _not_configured(kind, reason)
    return driver


@router.get("", response_model=NotificationChannelList)
async def list_channels(services: ServicesDep) -> NotificationChannelList:
    return NotificationChannelList(items=await services.channels.list())


@router.get("/kinds", response_model=ChannelKindList)
async def list_kinds(services: ServicesDep) -> ChannelKindList:
    """Every channel kind and whether this server can deliver to it."""
    items = []
    for kind in ChannelKind:
        driver = services.notifier.drivers.get(kind)
        reason = driver.unavailable() if driver else f"No {kind} driver on this server."
        items.append(ChannelKindInfo(kind=kind, available=reason is None, reason=reason))
    return ChannelKindList(items=items)


@router.post(
    "", status_code=201, response_model=NotificationChannel, responses=problem_responses(409, 422)
)
async def create_channel(
    data: NotificationChannelInput, services: ServicesDep
) -> NotificationChannel:
    try:
        return await services.channels.create(data)
    except (ChannelNameTaken, ChannelSecretRequired) as exc:
        raise _write_error(exc) from None


@router.post("/test", response_model=ChannelTestResult, responses=problem_responses(409, 422, 502))
async def send_test(body: ChannelTestRequest, services: ServicesDep) -> ChannelTestResult:
    """Send a short test message to a destination, saved or not; an omitted secret comes from
    ``channel_id``."""
    kind = body.destination.kind
    driver = _driver(services, kind)
    try:
        destination, secrets = await services.channels.secrets_for(
            body.destination, body.channel_id
        )
    except (ChannelSecretRequired, SecretsUnreadable, ChannelNotFound) as exc:
        raise _write_error(exc) from None
    try:
        detail = await driver.test(destination, secrets)
    except ChannelNotConfigured as exc:
        raise _not_configured(kind, exc.reason) from None
    except DeliveryError as exc:
        raise _failed("Sending the test message failed", exc.reason) from None
    except Exception as exc:  # never echo library messages: they may hold tokens
        raise _failed(
            "Sending the test message failed", f"Unexpected error: {type(exc).__name__}"
        ) from None
    return ChannelTestResult(kind=kind, detail=detail, sent_at=datetime.now(UTC))


@router.post(
    "/telegram/chats", response_model=TelegramChatList, responses=problem_responses(409, 422, 502)
)
async def telegram_chats(body: TelegramChatsRequest, services: ServicesDep) -> TelegramChatList:
    """A bot (its token, or the one stored for ``channel_id``) and the chats that recently
    messaged it or added it."""
    driver = _driver(services, ChannelKind.TELEGRAM)
    assert isinstance(driver, TelegramDriver)
    token = body.bot_token.get_secret_value().strip() if body.bot_token else None
    if token is None and body.channel_id is not None:
        try:
            _, secrets = await services.channels.resolve(body.channel_id)
        except (ChannelNotFound, SecretsUnreadable) as exc:
            raise _write_error(exc) from None
        token = secrets.get(BOT_TOKEN)
    if not token:
        raise validation_problem("body.bot_token", "enter the bot token")
    try:
        return await driver.chats(token)
    except TelegramApiError as exc:
        if exc.error_code == WEBHOOK_CONFLICT:
            raise ProblemError(
                409, ErrorCode.TELEGRAM_WEBHOOK_SET, "The bot has a webhook", exc.reason
            ) from None
        raise _failed("Telegram request failed", exc.reason) from None
    except DeliveryError as exc:
        raise _failed("Telegram request failed", exc.reason) from None


@router.get("/{channel_id}", response_model=NotificationChannel, responses=problem_responses(404))
async def get_channel(channel_id: str, services: ServicesDep) -> NotificationChannel:
    channel = await services.channels.get(channel_id)
    if channel is None:
        raise _not_found(channel_id)
    return channel


@router.put(
    "/{channel_id}",
    response_model=NotificationChannel,
    responses=problem_responses(404, 409, 422),
)
async def update_channel(
    channel_id: str, data: NotificationChannelInput, services: ServicesDep
) -> NotificationChannel:
    """Every project sending to the channel uses the new destination from its next run on."""
    try:
        channel = await services.channels.update(channel_id, data)
    except (ChannelNameTaken, ChannelSecretRequired, SecretsUnreadable) as exc:
        raise _write_error(exc) from None
    if channel is None:
        raise _not_found(channel_id)
    return channel


@router.delete(
    "/{channel_id}",
    status_code=204,
    response_class=Response,
    responses=problem_responses(404, 409),
)
async def delete_channel(channel_id: str, services: ServicesDep) -> Response:
    """Only a channel no schedule sends to can be deleted."""
    try:
        deleted = await services.channels.delete(channel_id)
    except ChannelInUse as exc:
        names = ", ".join(u.name for u in exc.used_by)
        raise ProblemError(
            409,
            ErrorCode.CHANNEL_IN_USE,
            "Notification channel is in use",
            f"Cannot delete it: the schedules of {names} send to it. Remove it there first.",
        ) from None
    if not deleted:
        raise _not_found(channel_id)
    return Response(status_code=204)
