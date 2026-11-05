"""SQLite repository of notification channels (T026): destinations with encrypted secrets.

A channel row stores its destination as JSON without secrets; a Telegram bot token is
encrypted in ``secrets`` (``{"bot_token": ...}``). Secrets are write-only: an update that omits
the token keeps the stored one while the channel stays a Telegram channel.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import TypeAdapter

from app.domain.common import format_utc
from app.domain.ids import new_channel_id
from app.domain.notifications import (
    AnyDestination,
    ChannelKind,
    ChannelUsage,
    Destination,
    EmailDestination,
    NotificationChannel,
    NotificationChannelInput,
    TelegramDestinationInput,
)
from app.storage.db import notification_channels, projects, schedule_notifications
from app.storage.secrets import SecretBox, SecretsUnreadable

BOT_TOKEN = "bot_token"
_DESTINATION: TypeAdapter[AnyDestination] = TypeAdapter(Destination)


class ChannelNameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"a notification channel named {name!r} already exists")
        self.name = name


class ChannelInUse(Exception):
    """Project schedules send to the channel, so it cannot be deleted."""

    def __init__(self, used_by: list[ChannelUsage]) -> None:
        super().__init__(f"used by {', '.join(u.name for u in used_by)}")
        self.used_by = used_by


class ChannelSecretRequired(Exception):
    """A secret was omitted but none of that kind is stored to keep; ``field`` is relative to
    the request body."""

    def __init__(self, field: str) -> None:
        super().__init__(f"{field} is required")
        self.field = field


class ChannelNotFound(Exception):
    pass


class SqliteChannelRepository:
    def __init__(self, engine: sa.Engine, box: SecretBox) -> None:
        self.engine = engine
        self.box = box
        self._lock = asyncio.Lock()

    async def _run[T](self, fn: Callable[[sa.Connection], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(self._tx, fn)

    def _tx[T](self, fn: Callable[[sa.Connection], T]) -> T:
        with self.engine.begin() as conn:
            return fn(conn)

    # --- reads ---------------------------------------------------------------------------

    @staticmethod
    def _usage(conn: sa.Connection, channel_id: str | None) -> dict[str, list[ChannelUsage]]:
        stmt = (
            sa.select(schedule_notifications.c.channel_id, projects.c.project_id, projects.c.name)
            .join(projects, projects.c.project_id == schedule_notifications.c.project_id)
            .order_by(projects.c.name)
        )
        if channel_id is not None:
            stmt = stmt.where(schedule_notifications.c.channel_id == channel_id)
        usage: dict[str, list[ChannelUsage]] = {}
        for row in conn.execute(stmt):
            usage.setdefault(row.channel_id, []).append(
                ChannelUsage(project_id=row.project_id, name=row.name)
            )
        return usage

    def _view(self, row: Any, used_by: list[ChannelUsage]) -> NotificationChannel:
        readable = True
        if row.secrets is not None:
            try:
                self.box.decrypt(row.secrets)
            except SecretsUnreadable:
                readable = False
        return NotificationChannel(
            channel_id=row.channel_id,
            name=row.name,
            description=row.description,
            destination=_destination(row),
            readable=readable,
            used_by=used_by,
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
        )

    def _load(self, conn: sa.Connection, channel_id: str | None) -> list[NotificationChannel]:
        stmt = sa.select(notification_channels).order_by(notification_channels.c.name)
        if channel_id is not None:
            stmt = stmt.where(notification_channels.c.channel_id == channel_id)
        usage = self._usage(conn, channel_id)
        return [self._view(row, usage.get(row.channel_id, [])) for row in conn.execute(stmt)]

    async def list(self) -> list[NotificationChannel]:
        return await self._run(lambda c: self._load(c, None))

    async def get(self, channel_id: str) -> NotificationChannel | None:
        found = await self._run(lambda c: self._load(c, channel_id))
        return found[0] if found else None

    async def resolve(self, channel_id: str) -> tuple[NotificationChannel, dict[str, str]]:
        """The channel and its decrypted secrets.

        Raises ChannelNotFound, and SecretsUnreadable when the secrets cannot be decrypted.
        """

        def q(conn: sa.Connection) -> tuple[NotificationChannel, bytes | None]:
            found = self._load(conn, channel_id)
            if not found:
                raise ChannelNotFound(channel_id)
            blob: bytes | None = conn.execute(
                sa.select(notification_channels.c.secrets).where(
                    notification_channels.c.channel_id == channel_id
                )
            ).scalar_one()
            return found[0], blob

        channel, blob = await self._run(q)
        return channel, self.box.decrypt(blob) if blob is not None else {}

    async def secrets_for(
        self, destination: EmailDestination | TelegramDestinationInput, channel_id: str | None
    ) -> tuple[AnyDestination, dict[str, str]]:
        """A draft destination as stored plus its secrets: a token it carries, else the one
        stored for ``channel_id``. Raises ChannelSecretRequired, ChannelNotFound and
        SecretsUnreadable."""
        if isinstance(destination, EmailDestination):
            return destination, {}
        stored: dict[str, str] = {}
        if destination.bot_token is None and channel_id is not None:
            _, stored = await self.resolve(channel_id)
        public, secrets = _split(destination, lambda: stored)
        return _DESTINATION.validate_python({**public, "bot_token_set": True}), secrets

    # --- writes --------------------------------------------------------------------------

    @staticmethod
    def _name_taken(conn: sa.Connection, name: str, channel_id: str | None) -> bool:
        stmt = sa.select(notification_channels.c.channel_id).where(
            notification_channels.c.name == name
        )
        if channel_id is not None:
            stmt = stmt.where(notification_channels.c.channel_id != channel_id)
        return conn.execute(stmt).first() is not None

    async def create(self, data: NotificationChannelInput) -> NotificationChannel:
        """Raises ChannelNameTaken and ChannelSecretRequired."""
        channel_id = new_channel_id()
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> NotificationChannel:
            if self._name_taken(conn, data.name, None):
                raise ChannelNameTaken(data.name)
            public, secrets = _split(data.destination, None)
            conn.execute(
                notification_channels.insert().values(
                    channel_id=channel_id,
                    name=data.name,
                    description=data.description,
                    kind=data.destination.kind.value,
                    destination=json.dumps(public, sort_keys=True),
                    secrets=self.box.encrypt(secrets) if secrets else None,
                    created_at=now,
                    updated_at=now,
                )
            )
            return self._load(conn, channel_id)[0]

        return await self._run(q)

    async def update(
        self, channel_id: str, data: NotificationChannelInput
    ) -> NotificationChannel | None:
        """An omitted secret is kept while the kind is unchanged.

        Raises ChannelNameTaken, ChannelSecretRequired and SecretsUnreadable.
        """
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> NotificationChannel | None:
            stored = conn.execute(
                sa.select(notification_channels).where(
                    notification_channels.c.channel_id == channel_id
                )
            ).first()
            if stored is None:
                return None
            if self._name_taken(conn, data.name, channel_id):
                raise ChannelNameTaken(data.name)
            kept = None
            if stored.kind == data.destination.kind.value and stored.secrets is not None:
                kept = lambda: self.box.decrypt(stored.secrets)  # noqa: E731
            public, secrets = _split(data.destination, kept)
            conn.execute(
                notification_channels.update()
                .where(notification_channels.c.channel_id == channel_id)
                .values(
                    name=data.name,
                    description=data.description,
                    kind=data.destination.kind.value,
                    destination=json.dumps(public, sort_keys=True),
                    secrets=self.box.encrypt(secrets) if secrets else None,
                    updated_at=now,
                )
            )
            return self._load(conn, channel_id)[0]

        return await self._run(q)

    async def delete(self, channel_id: str) -> bool:
        """Raises ChannelInUse while project schedules send to the channel."""

        def q(conn: sa.Connection) -> bool:
            if used_by := self._usage(conn, channel_id).get(channel_id):
                raise ChannelInUse(used_by)
            result = conn.execute(
                notification_channels.delete().where(
                    notification_channels.c.channel_id == channel_id
                )
            )
            return result.rowcount > 0

        return await self._run(q)


def _split(
    destination: EmailDestination | TelegramDestinationInput,
    kept: Callable[[], dict[str, str]] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Public JSON and secrets of a destination input. Raises ChannelSecretRequired."""
    public = destination.model_dump(mode="json", exclude={"bot_token"})
    if isinstance(destination, EmailDestination):
        return public, {}
    if destination.bot_token is not None:
        return public, {BOT_TOKEN: destination.bot_token.get_secret_value()}
    stored = kept() if kept is not None else {}
    if BOT_TOKEN not in stored:
        raise ChannelSecretRequired("destination.bot_token")
    return public, {BOT_TOKEN: stored[BOT_TOKEN]}


def _destination(row: Any) -> AnyDestination:
    public = json.loads(row.destination)
    if public["kind"] == ChannelKind.TELEGRAM.value:
        public["bot_token_set"] = row.secrets is not None
    return _DESTINATION.validate_python(public)
