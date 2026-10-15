"""SQLite CredentialRepository: source credentials with encrypted secrets (T021)."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa

from app.domain.auth import AuthType, NoAuth
from app.domain.common import SourceKind, format_utc
from app.domain.credentials import Credential, CredentialInput, CredentialUsage
from app.domain.ids import new_credential_id
from app.storage.auth import secret_value, secret_view, split_secret
from app.storage.db import app_meta, credentials, project_sources, projects
from app.storage.secrets import SecretBox, SecretsUnreadable

INLINE_IMPORT_KEY = "shared_credentials_import"
"""Set to 'pending' by migrations 0007/0008 when sources still stored their own secrets."""
INLINE_IMPORT_PENDING = "pending"
INLINE_IMPORT_DONE = "done"

_KIND_LABELS = {
    SourceKind.PROMETHEUS: "Prometheus",
    SourceKind.CLOUDFLARE: "Cloudflare",
    SourceKind.SENTRY: "Sentry",
    SourceKind.WAZUH: "Wazuh",
    SourceKind.GRAFANA: "Grafana",
}


class CredentialNameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"a credential named {name!r} already exists")
        self.name = name


class CredentialInUse(Exception):
    """The credential is referenced by sources, so it cannot be deleted or change kind."""

    def __init__(self, used_by: list[CredentialUsage]) -> None:
        super().__init__(f"used by {', '.join(u.name for u in used_by)}")
        self.used_by = used_by


class SqliteCredentialRepository:
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
    def _usage(conn: sa.Connection, credential_id: str | None) -> dict[str, list[CredentialUsage]]:
        stmt = (
            sa.select(project_sources.c.credential_id, projects.c.project_id, projects.c.name)
            .join(projects, projects.c.project_id == project_sources.c.project_id)
            .where(project_sources.c.credential_id.is_not(None))
            .order_by(projects.c.name)
        )
        if credential_id is not None:
            stmt = stmt.where(project_sources.c.credential_id == credential_id)
        usage: dict[str, list[CredentialUsage]] = {}
        for row in conn.execute(stmt):
            found = usage.setdefault(row.credential_id, [])
            if all(u.project_id != row.project_id for u in found):
                found.append(CredentialUsage(project_id=row.project_id, name=row.name))
        return usage

    def _view(self, row: Any, used_by: list[CredentialUsage]) -> Credential:
        auth = secret_view(json.loads(row.auth), True)
        if isinstance(auth, NoAuth):  # pragma: no cover - a credential always holds a secret
            raise ValueError(f"credential {row.credential_id} has no secret mechanism")
        try:
            self.box.decrypt(row.secrets)
            readable = True
        except SecretsUnreadable:
            readable = False
        return Credential(
            credential_id=row.credential_id,
            name=row.name,
            description=row.description,
            kind=row.kind,
            auth=auth,
            readable=readable,
            used_by=used_by,
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
        )

    def _load(self, conn: sa.Connection, credential_id: str | None) -> list[Credential]:
        stmt = sa.select(credentials).order_by(credentials.c.name)
        if credential_id is not None:
            stmt = stmt.where(credentials.c.credential_id == credential_id)
        usage = self._usage(conn, credential_id)
        return [self._view(row, usage.get(row.credential_id, [])) for row in conn.execute(stmt)]

    async def list(self) -> list[Credential]:
        return await self._run(lambda c: self._load(c, None))

    async def get(self, credential_id: str) -> Credential | None:
        found = await self._run(lambda c: self._load(c, credential_id))
        return found[0] if found else None

    # --- writes --------------------------------------------------------------------------

    @staticmethod
    def _name_taken(conn: sa.Connection, name: str, credential_id: str | None) -> bool:
        stmt = sa.select(credentials.c.credential_id).where(credentials.c.name == name)
        if credential_id is not None:
            stmt = stmt.where(credentials.c.credential_id != credential_id)
        return conn.execute(stmt).first() is not None

    async def create(self, data: CredentialInput) -> Credential:
        """Raises CredentialNameTaken, and SecretRequired when the secret is omitted."""
        credential_id = new_credential_id()
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> Credential:
            if self._name_taken(conn, data.name, None):
                raise CredentialNameTaken(data.name)
            public, secrets = split_secret(data.auth, None, None)
            conn.execute(
                credentials.insert().values(
                    credential_id=credential_id,
                    name=data.name,
                    description=data.description,
                    kind=data.kind.value,
                    auth=json.dumps(public, sort_keys=True),
                    secrets=self.box.encrypt(secrets),
                    created_at=now,
                    updated_at=now,
                )
            )
            return self._load(conn, credential_id)[0]

        return await self._run(q)

    async def update(self, credential_id: str, data: CredentialInput) -> Credential | None:
        """Omitted secrets are kept when the mechanism type is unchanged.

        Raises CredentialNameTaken, CredentialInUse (kind change), SecretRequired and
        SecretsUnreadable.
        """
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> Credential | None:
            stored = conn.execute(
                sa.select(credentials).where(credentials.c.credential_id == credential_id)
            ).first()
            if stored is None:
                return None
            if self._name_taken(conn, data.name, credential_id):
                raise CredentialNameTaken(data.name)
            used_by = self._usage(conn, credential_id).get(credential_id, [])
            if data.kind.value != stored.kind and used_by:
                raise CredentialInUse(used_by)
            public, secrets = split_secret(
                data.auth, json.loads(stored.auth), lambda: self.box.decrypt(stored.secrets)
            )
            conn.execute(
                credentials.update()
                .where(credentials.c.credential_id == credential_id)
                .values(
                    name=data.name,
                    description=data.description,
                    kind=data.kind.value,
                    auth=json.dumps(public, sort_keys=True),
                    secrets=self.box.encrypt(secrets),
                    updated_at=now,
                )
            )
            return self._load(conn, credential_id)[0]

        return await self._run(q)

    async def delete(self, credential_id: str) -> bool:
        """Raises CredentialInUse while sources reference the credential."""

        def q(conn: sa.Connection) -> bool:
            if used_by := self._usage(conn, credential_id).get(credential_id):
                raise CredentialInUse(used_by)
            result = conn.execute(
                credentials.delete().where(credentials.c.credential_id == credential_id)
            )
            return result.rowcount > 0

        return await self._run(q)

    async def move_inline_secrets(self) -> int | None:
        """Once after migrations 0007/0008: every secret still stored in a source becomes a
        credential that the source references; identical secrets (same kind, mechanism and
        value) share one. A secret that cannot be decrypted is moved as is, into a credential of
        its own that reports ``readable: false``.

        Returns the number of credentials created, or None when no import was pending.
        """
        now = format_utc(datetime.now(UTC))

        def q(conn: sa.Connection) -> int | None:
            pending = conn.execute(
                sa.select(app_meta.c.value).where(app_meta.c.key == INLINE_IMPORT_KEY)
            ).first()
            if pending is None or pending[0] != INLINE_IMPORT_PENDING:
                return None

            groups: dict[tuple[str, str, str], list[tuple[Any, dict[str, Any]]]] = {}
            blobs: dict[tuple[str, str, str], bytes] = {}
            rows = conn.execute(
                sa.select(project_sources)
                .where(project_sources.c.credential_id.is_(None))
                .order_by(project_sources.c.kind, project_sources.c.project_id)
            )
            for row in rows:
                config = json.loads(row.config)
                public = config["auth"]
                if row.secrets is None or public["type"] in INLINE_FREE_TYPES:
                    _relink(conn, row, config, None, now)
                    continue
                field = _secret_field(public)
                try:
                    value = secret_value(self.box.decrypt(row.secrets), field)
                except SecretsUnreadable:
                    value = None
                if value is None:  # unreadable: keep the ciphertext, one credential per source
                    key = (row.kind, json.dumps(public, sort_keys=True), f"#{row.project_id}")
                    blobs[key] = row.secrets
                else:
                    key = (row.kind, json.dumps(public, sort_keys=True), value)
                    blobs.setdefault(key, self.box.encrypt({field: value}))
                groups.setdefault(key, []).append((row, config))

            for key, members in groups.items():
                kind, public_json, _ = key
                credential_id = insert_credential(
                    conn,
                    kind=SourceKind(kind),
                    public=json.loads(public_json),
                    blob=blobs[key],
                    name=_import_name(SourceKind(kind), json.loads(public_json), members),
                    description=f"Moved on upgrade from {_projects_text(conn, members)}.",
                    now=now,
                )
                for row, config in members:
                    _relink(conn, row, config, credential_id, now)
            conn.execute(
                app_meta.update()
                .where(app_meta.c.key == INLINE_IMPORT_KEY)
                .values(value=INLINE_IMPORT_DONE)
            )
            return len(groups)

        return await self._run(q)


INLINE_FREE_TYPES = (AuthType.NONE.value, AuthType.CREDENTIAL.value)


def insert_credential(
    conn: sa.Connection,
    *,
    kind: SourceKind,
    public: dict[str, Any],
    blob: bytes,
    name: str,
    description: str | None,
    now: str,
) -> str:
    """Insert a credential holding the encrypted ``blob``, under a unique variant of ``name``."""
    taken = {n.lower() for (n,) in conn.execute(sa.select(credentials.c.name))}
    credential_id = new_credential_id()
    conn.execute(
        credentials.insert().values(
            credential_id=credential_id,
            name=_unique_name(name, taken),
            description=description,
            kind=kind.value,
            auth=json.dumps(public, sort_keys=True),
            secrets=blob,
            created_at=now,
            updated_at=now,
        )
    )
    return credential_id


def _relink(
    conn: sa.Connection, row: Any, config: dict[str, Any], credential_id: str | None, now: str
) -> None:
    """Point a source at ``credential_id`` (none without one) and drop its own secret."""
    config["auth"] = {"type": AuthType.CREDENTIAL.value if credential_id else AuthType.NONE.value}
    conn.execute(
        project_sources.update()
        .where(project_sources.c.project_id == row.project_id)
        .where(project_sources.c.kind == row.kind)
        .values(
            config=json.dumps(config, sort_keys=True),
            secrets=None,
            credential_id=credential_id,
            updated_at=now,
        )
    )
    conn.execute(
        projects.update().where(projects.c.project_id == row.project_id).values(updated_at=now)
    )


def _projects_text(conn: sa.Connection, members: list[tuple[Any, dict[str, Any]]]) -> str:
    ids = [row.project_id for row, _ in members]
    rows = conn.execute(
        sa.select(projects.c.name).where(projects.c.project_id.in_(ids)).order_by(projects.c.name)
    )
    return ", ".join(str(name) for (name,) in rows)


def _secret_field(public: dict[str, Any]) -> str:
    return "password" if public["type"] == AuthType.BASIC.value else "token"


def _import_name(
    kind: SourceKind, public: dict[str, Any], members: list[tuple[Any, dict[str, Any]]]
) -> str:
    """E.g. "Grafana token (grafana.example.com)" or "Wazuh reader@indexer"."""
    hosts = sorted(
        {urlsplit(c.get("url") or c.get("api_url") or "").hostname or "" for _, c in members}
    )
    host = hosts[0] if len(hosts) == 1 and kind is not SourceKind.CLOUDFLARE else ""
    label = _KIND_LABELS[kind]
    if public["type"] == AuthType.BASIC.value:
        return f"{label} {public['username']}@{host}" if host else f"{label} {public['username']}"
    mechanism = "token" if public["type"] == AuthType.TOKEN.value else public["type"]
    return f"{label} {mechanism} ({host})" if host else f"{label} {mechanism}"


def _unique_name(base: str, taken: set[str]) -> str:
    base = base[:95]
    name, n = base, 1
    while name.lower() in taken:
        n += 1
        name = f"{base} {n}"
    taken.add(name.lower())
    return name
