"""Minimal Telegram Bot API client (T027): the four methods notifications need.

The bot token is part of every request URL (``/bot<token>/<method>``), so nothing from
``httpx`` may escape this module: transport errors become ``TelegramApiError`` with the
exception's type name only, API errors carry Telegram's description with the token redacted,
and the ``httpx`` request log (INFO, with the URL) is silenced.
"""

import logging
from typing import Any, Protocol

import httpx

from app.notifications.drivers.base import DeliveryError, redact

# httpx logs every request URL at INFO; with the token in the path that would leak it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

CONNECT_TIMEOUT_SECONDS = 10.0
TIMEOUT_SECONDS = 15.0
UPDATES_LIMIT = 100


class TelegramApiError(DeliveryError):
    """A failed Bot API call; ``reason`` is actionable and holds no token."""

    def __init__(
        self, reason: str, *, error_code: int | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.retry_after = retry_after


class TelegramApi(Protocol):
    async def get_me(self) -> dict[str, Any]: ...

    async def get_chat(self, chat_id: str) -> dict[str, Any]: ...

    async def get_updates(self) -> list[dict[str, Any]]:
        """Pending updates, without confirming them (no offset): other consumers keep them."""
        ...

    async def send_message(
        self, chat_id: str, text: str, *, thread_id: int | None, silent: bool
    ) -> dict[str, Any]:
        """Send ``text`` in HTML parse mode without link previews."""
        ...


def chat_param(chat_id: str) -> int | str:
    """Numeric IDs as integers, ``@username`` as is."""
    return chat_id if chat_id.startswith("@") else int(chat_id)


def api_error(status: int, body: dict[str, Any], token: str) -> TelegramApiError:
    """An actionable reason for a Bot API error response."""
    code = body.get("error_code") if isinstance(body.get("error_code"), int) else status
    description = redact(str(body.get("description") or f"HTTP {status}"), token)
    raw_params = body.get("parameters")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    lowered = description.lower()
    retry_after = params.get("retry_after") if isinstance(params.get("retry_after"), int) else None
    if code == 401:
        reason = "Bot token rejected (401 Unauthorized): check the credential's token."
    elif isinstance(migrate := params.get("migrate_to_chat_id"), int):
        reason = f"The group was upgraded to a supergroup; set the chat ID to {migrate}."
    elif code == 429:
        wait = f" for {retry_after} s" if retry_after is not None else ""
        reason = f"Rate limited by Telegram{wait}."
    elif "chat not found" in lowered:
        reason = "Chat not found: check the chat ID and that the bot was added to the chat."
    elif "thread not found" in lowered:
        reason = "Topic not found: check the topic ID (message_thread_id)."
    elif code == 403:
        reason = f"The bot cannot post in this chat: {description}"
    elif code == 409 and "webhook" in lowered:
        reason = (
            "The bot has a webhook, so Telegram does not hand out its recent chats; "
            "enter the chat ID instead."
        )
    else:
        reason = f"Telegram API error {code}: {description}"
    return TelegramApiError(reason, error_code=code, retry_after=retry_after)


class BotApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    async def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/bot{self.token}/{method}"
        timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as http:
                response = await http.post(url, json=params or {})
        except httpx.HTTPError as exc:
            raise TelegramApiError(f"Telegram API unreachable: {type(exc).__name__}") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise TelegramApiError(
                f"Telegram API answered HTTP {response.status_code} without a JSON body.",
                error_code=response.status_code,
            )
        if body.get("ok") is True:
            return body.get("result")
        raise api_error(response.status_code, body, self.token)

    async def get_me(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("getMe")
        return result

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("getChat", {"chat_id": chat_param(chat_id)})
        return result

    async def get_updates(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._call(
            "getUpdates", {"limit": UPDATES_LIMIT, "timeout": 0}
        )
        return result

    async def send_message(
        self, chat_id: str, text: str, *, thread_id: int | None, silent: bool
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_param(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "disable_notification": silent,
        }
        if thread_id is not None:
            params["message_thread_id"] = thread_id
        result: dict[str, Any] = await self._call("sendMessage", params)
        return result
