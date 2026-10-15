"""SQLite ProjectRepository: projects and per-kind source configs with encrypted secrets.

A source row stores its non-secret config as JSON, including the public part of its auth
mechanism (``config["auth"]``); the secret is stored encrypted in the row, or comes from the
shared credential referenced by ``credential_id`` (T021).
"""

import asyncio
import builtins
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, TypeAdapter

from app.domain.auth import (
    AuthType,
    BasicAuthInput,
    BearerAuthInput,
    CredentialAuth,
    CredentialRef,
    NoAuth,
)
from app.domain.common import LabelMatcher, SourceKind, format_utc
from app.domain.ids import new_project_id
from app.domain.projects import (
    SECRET_REQUIRED_KINDS,
    AnySource,
    CloudflareConnection,
    GrafanaConnection,
    Project,
    ProjectInput,
    PrometheusConnection,
    PrometheusSourceInput,
    SentryConnection,
    Source,
    SourceConnection,
    SourceInput,
    WazuhConnection,
    synthetic_url,
)
from app.domain.schedule import ReportSchedule
from app.storage.auth import (
    CredentialInvalid,
    SecretRequired,
    connection_auth,
    secret_view,
    split_secret,
)
from app.storage.db import app_meta, credentials, project_sources, projects
from app.storage.secrets import SecretBox, SecretsUnreadable

__all__ = ["CredentialInvalid", "ProjectNameTaken", "SecretRequired", "SqliteProjectRepository"]

LEGACY_IMPORT_KEY = "legacy_source_import"
"""Set to 'pending' by migration 0003 when it created projects from existing reports."""
LEGACY_IMPORT_PENDING = "pending"
LEGACY_IMPORT_DONE = "done"

_SOURCE_VIEW: TypeAdapter[AnySource] = TypeAdapter(Source)
_CONNECTIONS: dict[SourceKind, type[BaseModel]] = {
    SourceKind.PROMETHEUS: PrometheusConnection,
    SourceKind.CLOUDFLARE: CloudflareConnection,
    SourceKind.SENTRY: SentryConnection,
    SourceKind.WAZUH: WazuhConnection,
    SourceKind.GRAFANA: GrafanaConnection,
}

_SOURCES = sa.select(
    project_sources,
    credentials.c.name.label("credential_name"),
    credentials.c.auth.label("credential_auth"),
    credentials.c.secrets.label("credential_secrets"),
).select_from(
    project_sources.outerjoin(
        credentials, project_sources.c.credential_id == credentials.c.credential_id
    )
)
"""Source rows with the shared credential they reference, if any."""


class ProjectNameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"a project named {name!r} already exists")
        self.name = name


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
        src_stmt = _SOURCES
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
            _, blob = _effective_auth(src)
            if blob is not None:
                try:
                    self.box.decrypt(blob)
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

    def _connection(
        self, kind: SourceKind, config: dict[str, Any], public: dict[str, Any], blob: bytes | None
    ) -> SourceConnection:
        """Decrypted connection; raises SecretsUnreadable."""
        secrets = self.box.decrypt(blob) if blob is not None else {}
        fields = {k: v for k, v in config.items() if k != "auth"}
        connection = _CONNECTIONS[kind].model_validate(
            {**fields, **connection_auth(kind, public, secrets)}
        )
        return connection  # type: ignore[return-value]

    async def prometheus_connection(self, project_id: str) -> PrometheusConnection | None:
        """Decrypted connection for a stored project; raises SecretsUnreadable."""
        found = await self.connections(project_id)
        return next((c for c in found if isinstance(c, PrometheusConnection)), None)

    async def connections(self, project_id: str) -> builtins.list[SourceConnection]:
        """Decrypted connections of every stored source, by kind; raises SecretsUnreadable."""

        def q(conn: sa.Connection) -> builtins.list[Any]:
            return builtins.list(
                conn.execute(
                    _SOURCES.where(project_sources.c.project_id == project_id).order_by(
                        project_sources.c.kind
                    )
                )
            )

        found: builtins.list[SourceConnection] = []
        for row in await self._run(q):
            public, blob = _effective_auth(row)
            found.append(
                self._connection(SourceKind(row.kind), json.loads(row.config), public, blob)
            )
        return found

    async def resolve_connection(
        self, source: SourceInput, project_id: str | None
    ) -> SourceConnection:
        """Connection for a draft source, filling omitted secrets from the stored project.

        Raises SecretRequired, CredentialInvalid and SecretsUnreadable.
        """

        def q(conn: sa.Connection) -> tuple[dict[str, Any], dict[str, Any], bytes | None]:
            stored = self._stored(conn, project_id, source.kind) if project_id else None
            config, secrets, credential_id = self._split(conn, source, stored)
            if credential_id is None:
                return config, config["auth"], self.box.encrypt(secrets) if secrets else None
            row = conn.execute(
                sa.select(credentials.c.auth, credentials.c.secrets).where(
                    credentials.c.credential_id == credential_id
                )
            ).one()
            return config, json.loads(row.auth), row.secrets

        config, public, blob = await self._run(q)
        return self._connection(source.kind, config, public, blob)

    # --- writes --------------------------------------------------------------------------

    def _split(
        self, conn: sa.Connection, source: SourceInput, stored: Any
    ) -> tuple[dict[str, Any], dict[str, str], str | None]:
        """Non-secret config, the secrets to store and the referenced credential.

        Omitted secrets are kept from ``stored``. Raises SecretRequired and CredentialInvalid
        with the field path relative to the source.
        """
        config = source.model_dump(mode="json", exclude={"kind", "auth"})
        secrets: dict[str, str] = {}
        credential_id: str | None = None
        match source.auth:
            case NoAuth():
                if source.kind in SECRET_REQUIRED_KINDS and not synthetic_url(config["api_url"]):
                    raise SecretRequired("auth")
                config["auth"] = {"type": AuthType.NONE.value}
            case CredentialRef() as ref:
                _check_credential(conn, ref.credential_id, source.kind)
                credential_id = ref.credential_id
                config["auth"] = {"type": AuthType.CREDENTIAL.value}
            case auth:
                own = stored if stored is not None and stored.credential_id is None else None
                stored_auth = json.loads(own.config)["auth"] if own is not None else None
                blob = own.secrets if own is not None else None
                config["auth"], secrets = split_secret(
                    auth, stored_auth, (lambda: self.box.decrypt(blob)) if blob else None
                )
        return config, secrets, credential_id

    def _write_sources(
        self,
        conn: sa.Connection,
        project_id: str,
        data: ProjectInput,
        now: str,
        secrets_from: str | None = None,
    ) -> None:
        """Replace the project's sources; omitted secrets come from ``secrets_from`` or itself."""
        rows = []
        for i, source in enumerate(data.sources):
            stored = self._stored(conn, secrets_from or project_id, source.kind)
            try:
                config, secrets, credential_id = self._split(conn, source, stored)
            except SecretRequired as exc:
                raise SecretRequired(f"{i}.{exc.field}") from None
            except CredentialInvalid as exc:
                raise CredentialInvalid(f"{i}.{exc.field}", exc.message) from None
            rows.append(
                {
                    "project_id": project_id,
                    "kind": source.kind.value,
                    "config": json.dumps(config, sort_keys=True),
                    "secrets": self.box.encrypt(secrets) if secrets else None,
                    "credential_id": credential_id,
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

    async def create(self, data: ProjectInput, secrets_from: str | None = None) -> Project:
        """Create a project; omitted secrets are copied from project ``secrets_from`` (clone)."""
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
            self._write_sources(conn, project_id, data, now, secrets_from)
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
                config, secrets, _ = self._split(c, source, None)
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


def _effective_auth(row: Any) -> tuple[dict[str, Any], bytes | None]:
    """Public auth and encrypted secrets of a source row: its own or its credential's."""
    if row.credential_id is not None:
        return json.loads(row.credential_auth), row.credential_secrets
    return json.loads(row.config)["auth"], row.secrets


def _source_view(row: Any) -> AnySource:
    config = json.loads(row.config)
    public = config.pop("auth")
    if row.credential_id is not None:
        auth: BaseModel = CredentialAuth(credential_id=row.credential_id, name=row.credential_name)
    else:
        auth = secret_view(public, row.secrets is not None)
    return _SOURCE_VIEW.validate_python({**config, "kind": row.kind, "auth": auth.model_dump()})


def _check_credential(conn: sa.Connection, credential_id: str, kind: SourceKind) -> None:
    found = conn.execute(
        sa.select(credentials.c.kind).where(credentials.c.credential_id == credential_id)
    ).first()
    if found is None:
        raise CredentialInvalid("auth.credential_id", "no such credential")
    if found.kind != kind.value:
        raise CredentialInvalid(
            "auth.credential_id", f"a {found.kind} credential cannot be used by a {kind} source"
        )
