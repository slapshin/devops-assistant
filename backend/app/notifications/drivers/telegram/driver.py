"""Telegram driver (T027): posts the report into a chat with the channel's bot."""

import asyncio
from collections.abc import Callable
from typing import Any

from app.domain.notifications import (
    AnyDestination,
    ChannelKind,
    TelegramChat,
    TelegramChatList,
    TelegramDestination,
)
from app.notifications.drivers.base import ChannelNotConfigured, DeliveryError
from app.notifications.drivers.telegram.client import TelegramApi, TelegramApiError
from app.notifications.drivers.telegram.render import TEST_TEXT, render_telegram
from app.notifications.message import ReportMessage

BOT_TOKEN = "bot_token"
NOT_CONFIGURED = "Telegram delivery is turned off (TELEGRAM_PROVIDER=none)."
MAX_RETRY_AFTER_SECONDS = 5
"""A 429 asking to wait at most this long is retried once; longer waits fail."""
CHAT_UPDATE_KEYS = ("message", "edited_message", "channel_post", "my_chat_member", "chat_member")

ApiFactory = Callable[[str], TelegramApi]
"""Opens the Bot API for a bot token."""


class TelegramDriver:
    kind = ChannelKind.TELEGRAM

    def __init__(self, api: ApiFactory | None) -> None:
        self.api = api

    def unavailable(self) -> str | None:
        return NOT_CONFIGURED if self.api is None else None

    def open(self, token: str | None) -> TelegramApi:
        if self.api is None:
            raise ChannelNotConfigured(NOT_CONFIGURED)
        if not token:
            raise DeliveryError("The channel has no bot token; enter it again.")
        return self.api(token)

    async def deliver(
        self,
        destination: AnyDestination,
        secrets: dict[str, str],
        message: ReportMessage,
        *,
        silent: bool,
    ) -> int:
        chat = _telegram(destination)
        api = self.open(secrets.get(BOT_TOKEN))
        text = render_telegram(message)
        try:
            await _send(api, chat, text, silent)
        except TelegramApiError as exc:
            if exc.retry_after is None or exc.retry_after > MAX_RETRY_AFTER_SECONDS:
                raise
            await asyncio.sleep(exc.retry_after)
            await _send(api, chat, text, silent)
        return 1

    async def test(self, destination: AnyDestination, secrets: dict[str, str]) -> str:
        chat = _telegram(destination)
        api = self.open(secrets.get(BOT_TOKEN))
        found = await api.get_chat(chat.chat_id)
        await _send(api, chat, TEST_TEXT, silent=False)
        return f"sent to {chat_title(found)} ({found.get('type', 'chat')})"

    async def chats(self, token: str | None) -> TelegramChatList:
        """The bot and the chats in its pending updates, most recent first."""
        api = self.open(token)
        me = await api.get_me()
        found: dict[str, TelegramChat] = {}
        for update in reversed(await api.get_updates()):
            for key in CHAT_UPDATE_KEYS:
                raw = update.get(key)
                chat = raw.get("chat") if isinstance(raw, dict) else None
                if isinstance(chat, dict) and "id" in chat and str(chat["id"]) not in found:
                    found[str(chat["id"])] = TelegramChat(
                        chat_id=str(chat["id"]),
                        title=chat_title(chat),
                        type=str(chat.get("type", "chat")),
                        username=chat.get("username"),
                        is_forum=bool(chat.get("is_forum", False)),
                    )
        username = me.get("username")
        return TelegramChatList(
            bot_username=username if isinstance(username, str) else None,
            items=list(found.values()),
        )


async def _send(api: TelegramApi, chat: TelegramDestination, text: str, silent: bool) -> None:
    await api.send_message(chat.chat_id, text, thread_id=chat.message_thread_id, silent=silent)


def chat_title(chat: dict[str, Any]) -> str:
    if title := chat.get("title"):
        return str(title)
    name = " ".join(str(chat[k]) for k in ("first_name", "last_name") if chat.get(k))
    if name:
        return name
    return f"@{chat['username']}" if chat.get("username") else str(chat.get("id", "chat"))


def _telegram(destination: AnyDestination) -> TelegramDestination:
    if not isinstance(destination, TelegramDestination):  # pragma: no cover - registry routing
        raise TypeError(f"not a Telegram destination: {destination.kind}")
    return destination
