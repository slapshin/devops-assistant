"""SQLite ProjectRepository: projects and per-kind source configs with encrypted secrets."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import SecretStr

from app.domain.common import LabelMatcher, format_utc
from app.domain.ids import new_project_id
from app.domain.projects import (
    AuthType,
    BasicAuth,
    BasicAuthInput,
    BearerAuth,
    BearerAuthInput,
    NoAuth,
    Project,
    ProjectInput,
    PrometheusConnection,
    PrometheusSource,
    PrometheusSourceInput,
    SourceKind,
)
from app.domain.schedule import ReportSchedule
from app.storage.db import app_meta, project_sources, projects
from app.storage.secrets import SecretBox, SecretsUnreadable

LEGACY_IMPORT_KEY = "legacy_source_import"
"""Set to 'pending' by migration 0003 when it created projects from existing reports."""
LEGACY_IMPORT_PENDING = "pending"
LEGACY_IMPORT_DONE = "done"


class ProjectNameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"a project named {name!r} already exists")
        self.name = name


class SecretRequired(Exception):
    """A secret was omitted but nothing of that kind is stored to keep."""

    def __init__(self, field: str) -> None:
        super().__init__(f"{field} is required")
        self.field = field


class SqliteProjectRepository:
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

    def _load(self, conn: sa.Connection, project_id: str | None) -> list[Project]:
        stmt = sa.select(projects).order_by(projects.c.name)
        src_stmt = sa.select(project_sources)
        if project_id is not None:
            stmt = stmt.where(projects.c.project_id == project_id)
            src_stmt = src_stmt.where(project_sources.c.project_id == project_id)

        sources: dict[str, list[Any]] = {}
        for row in conn.execute(src_stmt.order_by(project_sources.c.kind)):
            sources.setdefault(row.project_id, []).append(row)
        now = datetime.now(UTC)
        return [
            self._project(row, sources.get(row.project_id, []), now) for row in conn.execute(stmt)
        ]

    def _project(self, row: Any, source_rows: list[Any], now: datetime) -> Project:
        readable = True
        for src in source_rows:
            if src.secrets is not None:
                try:
                    self.box.decrypt(src.secrets)
                except SecretsUnreadable:
                    readable = False
        schedule = _schedule(row.schedule)
        return Project(
            project_id=row.project_id,
            name=row.name,
            description=row.description,
            matchers=[LabelMatcher.model_validate(m) for m in json.loads(row.matchers)],
            sources=[_source_view(src) for src in source_rows],
            credentials_readable=readable,
            schedule=schedule,
            keep_reports=row.keep_reports,
            next_scheduled_run=schedule.next_after(now) if schedule else None,
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
        )

    async def scheduled(self) -> list[tuple[Project, datetime]]:
        """Projects with a schedule, each with the instant its runs are handled up to."""

        def q(conn: sa.Connection) -> list[tuple[Project, datetime]]:
            rows = conn.execute(
                sa.select(projects.c.project_id, projects.c.schedule_anchor).where(
                    projects.c.schedule.is_not(None)
                )
            )
            anchors = {project_id: anchor for project_id, anchor in rows}
            found = [p for p in self._load(conn, None) if p.project_id in anchors]
            return [(p, datetime.fromisoformat(anchors[p.project_id])) for p in found]

        return await self._run(q)

    async def advance_schedule(self, project_id: str, handled_until: datetime) -> None:
        """Mark scheduled runs up to ``handled_until`` handled; never moves the anchor back."""
        at = format_utc(handled_until)

        def q(conn: sa.Connection) -> None:
            conn.execute(
                projects.update()
                .where(projects.c.project_id == project_id)
                .where(projects.c.schedule_anchor < at)
                .values(schedule_anchor=at)
            )

        await self._run(q)

    async def list(self) -> list[Project]:
        return await self._run(lambda c: self._load(c, None))

    async def get(self, project_id: str) -> Project | None:
        found = await self._run(lambda c: self._load(c, project_id))
        return found[0] if found else None

    def _stored(self, conn: sa.Connection, project_id: str, kind: SourceKind) -> Any:
        return conn.execute(
            sa.select(project_sources)
            .where(project_sources.c.project_id == project_id)
            .where(project_sources.c.kind == kind.value)
        ).first()

    async def prometheus_connection(self, project_id: str) -> PrometheusConnection | None:
        """Decrypted connection for a stored project; raises SecretsUnreadable."""
        row = await self._run(lambda c: self._stored(c, project_id, SourceKind.PROMETHEUS))
        if row is None:
            return None
        config = json.loads(row.config)
        secrets = self.box.decrypt(row.secrets) if row.secrets is not None else {}
        return _connection(config, secrets)

    async def resolve_connection(
        self, source: PrometheusSourceInput, project_id: str | None
    ) -> PrometheusConnection:
        """Connection for a draft source, filling omitted secrets from the stored project."""
        row = None
        if project_id is not None:
            row = await self._run(lambda c: self._stored(c, project_id, SourceKind.PROMETHEUS))
        config, secrets = self._split(source, row)
        return _connection(config, secrets)

    # --- writes --------------------------------------------------------------------------

    def _split(
        self, source: PrometheusSourceInput, stored: Any
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Non-secret config and the secrets to store, keeping omitted secrets from ``stored``."""
        auth = source.auth
        config: dict[str, Any] = {
            "url": source.url,
            "tls_verify": source.tls_verify,
            "auth": {"type": auth.type.value},
        }

        def kept(field: str) -> dict[str, str]:
            stored_type = json.loads(stored.config)["auth"]["type"] if stored else None
            if stored_type != auth.type.value or stored.secrets is None:
                raise SecretRequired(f"auth.{field}")
            return self.box.decrypt(stored.secrets)

        match auth:
            case BearerAuthInput(token=token):
                secrets = {"token": token.get_secret_value()} if token else kept("token")
            case BasicAuthInput(username=username, password=password):
                config["auth"]["username"] = username
                secrets = (
                    {"password": password.get_secret_value()} if password else kept("password")
                )
            case _:
                secrets = {}
        return config, secrets

    def _write_sources(
        self, conn: sa.Connection, project_id: str, data: ProjectInput, now: str
    ) -> None:
        rows = []
        for source in data.sources:
            config, secrets = self._split(source, self._stored(conn, project_id, source.kind))
            rows.append(
                {
                    "project_id": project_id,
                    "kind": source.kind.value,
                    "config": json.dumps(config, sort_keys=True),
                    "secrets": self.box.encrypt(secrets) if secrets else None,
                    "updated_at": now,
                }
            )
        conn.execute(project_sources.delete().where(project_sources.c.project_id == project_id))
        if rows:
            conn.execute(project_sources.insert(), rows)

    @staticmethod
    def _values(data: ProjectInput) -> dict[str, Any]:
        return {
            "name": data.name,
            "description": data.description,
            "matchers": json.dumps([m.model_dump() for m in data.matchers]),
            "schedule": data.schedule.model_dump_json() if data.schedule else None,
            "keep_reports": data.keep_reports,
        }

    @staticmethod
    def _name_taken(conn: sa.Connection, name: str, project_id: str | None) -> bool:
        stmt = sa.select(projects.c.project_id).where(projects.c.name == name)
        if project_id is not None:
            stmt = stmt.where(projects.c.project_id != project_id)
        return conn.execute(stmt).first() is not None

    async def create(self, data: ProjectInput) -> Project:
        project_id = new_project_id()
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> Project:
            if self._name_taken(conn, data.name, None):
                raise ProjectNameTaken(data.name)
            conn.execute(
                projects.insert().values(
                    project_id=project_id,
                    created_at=now,
                    updated_at=now,
                    schedule_anchor=now if data.schedule else None,
                    **self._values(data),
                )
            )
            self._write_sources(conn, project_id, data, now)
            return self._load(conn, project_id)[0]

        return await self._run(q)

    async def update(self, project_id: str, data: ProjectInput) -> Project | None:
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> Project | None:
            stored = conn.execute(
                sa.select(projects.c.schedule).where(projects.c.project_id == project_id)
            ).first()
            if stored is None:
                return None
            if self._name_taken(conn, data.name, project_id):
                raise ProjectNameTaken(data.name)
            values = self._values(data)
            if _schedule(stored.schedule) != data.schedule:
                # A changed schedule starts now: it never fires for earlier times.
                values["schedule_anchor"] = now if data.schedule else None
            conn.execute(
                projects.update()
                .where(projects.c.project_id == project_id)
                .values(updated_at=now, **values)
            )
            self._write_sources(conn, project_id, data, now)
            return self._load(conn, project_id)[0]

        return await self._run(q)

    async def delete(self, project_id: str) -> bool:
        """Delete the project; its sources, jobs, and reports go with it (FK cascade)."""

        def q(conn: sa.Connection) -> bool:
            result = conn.execute(projects.delete().where(projects.c.project_id == project_id))
            return result.rowcount > 0

        return await self._run(q)

    async def count(self) -> int:
        def q(conn: sa.Connection) -> int:
            return int(conn.execute(sa.select(sa.func.count()).select_from(projects)).scalar_one())

        return await self._run(q)

    async def import_legacy_source(self, conn: PrometheusConnection | None) -> int | None:
        """Once after migration 0003: give migrated projects the deprecated METRICS_* source.

        Returns the number of projects updated, or None when no import was pending.
        """
        source = None
        if conn is not None:
            source = PrometheusSourceInput(
                url=conn.url,
                tls_verify=conn.tls_verify,
                auth=_auth_input(conn),
            )
        now = format_utc(datetime.now(UTC))

        def q(c: sa.Connection) -> int | None:
            pending = c.execute(
                sa.select(app_meta.c.value).where(app_meta.c.key == LEGACY_IMPORT_KEY)
            ).first()
            if pending is None or pending[0] != LEGACY_IMPORT_PENDING:
                return None

            updated = 0
            if source is not None:
                without = c.execute(
                    sa.select(projects.c.project_id).where(
                        ~sa.exists().where(project_sources.c.project_id == projects.c.project_id)
                    )
                ).all()
                config, secrets = self._split(source, None)
                for (project_id,) in without:
                    c.execute(
                        project_sources.insert().values(
                            project_id=project_id,
                            kind=SourceKind.PROMETHEUS.value,
                            config=json.dumps(config, sort_keys=True),
                            secrets=self.box.encrypt(secrets) if secrets else None,
                            updated_at=now,
                        )
                    )
                    c.execute(
                        projects.update()
                        .where(projects.c.project_id == project_id)
                        .values(updated_at=now)
                    )
                    updated += 1
            c.execute(
                app_meta.update()
                .where(app_meta.c.key == LEGACY_IMPORT_KEY)
                .values(value=LEGACY_IMPORT_DONE)
            )
            return updated

        return await self._run(q)


def _auth_input(conn: PrometheusConnection) -> NoAuth | BearerAuthInput | BasicAuthInput:
    if conn.bearer_token is not None:
        return BearerAuthInput(token=conn.bearer_token)
    if conn.basic_auth_user and conn.basic_auth_password is not None:
        return BasicAuthInput(username=conn.basic_auth_user, password=conn.basic_auth_password)
    return NoAuth()


def _schedule(raw: str | None) -> ReportSchedule | None:
    return ReportSchedule.model_validate_json(raw) if raw else None


def _source_view(row: Any) -> PrometheusSource:
    config = json.loads(row.config)
    auth = config["auth"]
    match AuthType(auth["type"]):
        case AuthType.BEARER:
            view: NoAuth | BearerAuth | BasicAuth = BearerAuth(token_set=row.secrets is not None)
        case AuthType.BASIC:
            view = BasicAuth(username=auth["username"], password_set=row.secrets is not None)
        case AuthType.NONE:
            view = NoAuth()
    return PrometheusSource(url=config["url"], tls_verify=config["tls_verify"], auth=view)


def _connection(config: dict[str, Any], secrets: dict[str, str]) -> PrometheusConnection:
    auth = config["auth"]
    token, password = secrets.get("token"), secrets.get("password")
    return PrometheusConnection(
        url=config["url"],
        tls_verify=config["tls_verify"],
        bearer_token=SecretStr(token) if token else None,
        basic_auth_user=auth.get("username"),
        basic_auth_password=SecretStr(password) if password else None,
    )
