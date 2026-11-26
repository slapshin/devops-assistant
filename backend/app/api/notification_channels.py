"""Notification channel routes (T026, T027): CRUD, kinds the server can deliver to, test
messages, and Telegram chat lookup. Secrets are write-only and never echoed in problems."""

from fastapi import APIRouter, Response

from app.api.problems import ProblemError, problem_responses, validation_problem
from app.api.routes import ServicesDep
from app.domain.jobs import ErrorCode
from app.domain.notifications import (
    ChannelKind,
    ChannelKindList,
    ChannelTestRequest,
    ChannelTestResult,
    NotificationChannel,
    NotificationChannelInput,
    NotificationChannelList,
    TelegramChatList,
    TelegramChatsRequest,
)
from app.notifications.operations import (
    OperationFailed,
    OperationNotConfigured,
    TelegramWebhookSet,
    TokenRequired,
)
from app.storage.channels import (
    ChannelInUse,
    ChannelNameTaken,
    ChannelNotFound,
    ChannelSecretRequired,
)
from app.storage.secrets import SecretsUnreadable

router = APIRouter(prefix="/api/notification-channels", tags=["notification channels"])


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


def _operation_error(exc: Exception) -> ProblemError:
    match exc:
        case OperationNotConfigured(kind=kind, reason=reason):
            return _not_configured(kind, reason)
        case OperationFailed(title=title, reason=reason):
            return _failed(title, reason)
        case TelegramWebhookSet(reason=reason):
            return ProblemError(
                409, ErrorCode.TELEGRAM_WEBHOOK_SET, "The bot has a webhook", reason
            )
        case TokenRequired():
            return validation_problem("body.bot_token", "enter the bot token")
    return _write_error(exc)


OPERATION_ERRORS = (
    OperationNotConfigured,
    OperationFailed,
    TelegramWebhookSet,
    TokenRequired,
    ChannelSecretRequired,
    SecretsUnreadable,
    ChannelNotFound,
)


@router.get("", response_model=NotificationChannelList)
async def list_channels(services: ServicesDep) -> NotificationChannelList:
    return NotificationChannelList(items=await services.channels.list())


@router.get("/kinds", response_model=ChannelKindList)
async def list_kinds(services: ServicesDep) -> ChannelKindList:
    """Every channel kind and whether this server can deliver to it."""
    return ChannelKindList(items=services.channel_operations.kinds())


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
    try:
        return await services.channel_operations.send_test(body.destination, body.channel_id)
    except OPERATION_ERRORS as exc:
        raise _operation_error(exc) from None


@router.post(
    "/telegram/chats", response_model=TelegramChatList, responses=problem_responses(409, 422, 502)
)
async def telegram_chats(body: TelegramChatsRequest, services: ServicesDep) -> TelegramChatList:
    """A bot (its token, or the one stored for ``channel_id``) and the chats that recently
    messaged it or added it."""
    try:
        return await services.channel_operations.telegram_chats(body.bot_token, body.channel_id)
    except OPERATION_ERRORS as exc:
        raise _operation_error(exc) from None


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
