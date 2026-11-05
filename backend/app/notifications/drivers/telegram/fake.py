"""``TELEGRAM_PROVIDER=fake``: a Bot API stand-in for tests and offline demos (T027).

Never contacts Telegram. Each sent message is written to ``directory`` as
``<UTC time>-<n>.json`` (method and parameters, never the token); chats are synthetic.
"""

import asyncio
import json
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

from app.notifications.drivers.telegram.client import chat_param

FAKE_BOT = {"id": 1000001, "is_bot": True, "first_name": "DevOps Assistant", "username": "demo_bot"}
FAKE_CHAT = {"id": -1001000000001, "type": "supergroup", "title": "Ops (demo)"}

_counter = count(1)


class FakeTelegramApi:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def get_me(self) -> dict[str, Any]:
        return dict(FAKE_BOT)

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        if chat_id.startswith("@"):
            return {"id": -1001000000002, "type": "channel", "title": chat_id[1:]}
        if int(chat_id) > 0:
            return {"id": int(chat_id), "type": "private", "first_name": "Demo user"}
        return {"id": int(chat_id), "type": "supergroup", "title": f"Chat {chat_id}"}

    async def get_updates(self) -> list[dict[str, Any]]:
        return [{"update_id": 1, "message": {"message_id": 1, "chat": dict(FAKE_CHAT)}}]

    async def send_message(
        self, chat_id: str, text: str, *, thread_id: int | None, silent: bool
    ) -> dict[str, Any]:
        record = {
            "method": "sendMessage",
            "chat_id": chat_param(chat_id),
            "message_thread_id": thread_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.directory / f"{stamp}-{next(_counter)}.json"
        await asyncio.to_thread(self._write, path, json.dumps(record, ensure_ascii=False, indent=2))
        return {"message_id": 1, "chat": {"id": chat_param(chat_id)}}

    def _write(self, path: Path, data: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
