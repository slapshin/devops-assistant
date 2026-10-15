"""Shared credentials: CRUD API, source references, rotation, and migration 0006 (T021)."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.domain.projects import (
    CloudflareConnection,
    GrafanaConnection,
    WazuhConnection,
    WazuhSourceInput,
)
from app.main import create_app
from app.settings import load_settings
from app.storage.db import MIGRATIONS, make_engine, migrate
from app.storage.secrets import SecretBox

TOKEN = "glsa_s3cr3t-token-value"
ROTATED = "glsa_r0tated-token-value"
ZONE = "0" * 32


def make_client(tmp_path: Path, **settings: Any) -> TestClient:
    config = {"_env_file": None, "data_dir": tmp_path, "ai_provider": "fake", **settings}
    return TestClient(create_app(load_settings(**config)))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def credential_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Grafana production",
        "kind": "grafana",
        "auth": {"type": "token", "token": TOKEN},
        **overrides,
    }


def grafana_project(credential_id: str, name: str = "Shop") -> dict[str, Any]:
    return {
        "name": name,
        "sources": [
            {
                "kind": "grafana",
                "api_url": "https://grafana.example.com",
                "folders": ["Shop"],
                "auth": {"type": "credential", "credential_id": credential_id},
            }
        ],
    }


def create_credential(client: TestClient, **overrides: Any) -> dict[str, Any]:
    res = client.post("/api/credentials", json=credential_body(**overrides))
    assert res.status_code == 201, res.text
    created: dict[str, Any] = res.json()
    return created


def services(client: TestClient) -> Any:
    return client.app.state.services  # type: ignore[attr-defined]


# --- credential CRUD --------------------------------------------------------------------------


def test_credential_secret_is_write_only(tmp_path: Path, client: TestClient) -> None:
    credential = create_credential(client, description="  Viewer  ")

    assert credential["name"] == "Grafana production"
    assert credential["description"] == "Viewer"
    assert credential["kind"] == "grafana"
    assert credential["auth"] == {"type": "token", "token_set": True}
    assert (credential["readable"], credential["used_by"]) == (True, [])
    assert credential["credential_id"]

    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        auth, secrets = db.execute("SELECT auth, secrets FROM credentials").fetchone()

    assert TOKEN not in auth and TOKEN.encode() not in secrets
    for path in ("/api/credentials", f"/api/credentials/{credential['credential_id']}"):
        assert TOKEN not in client.get(path).text


def test_credential_validation(client: TestClient) -> None:
    create_credential(client)

    taken = client.post("/api/credentials", json=credential_body(name="grafana PRODUCTION"))
    assert (taken.status_code, taken.json()["code"]) == (409, "credential_name_taken")

    wrong_type = client.post(
        "/api/credentials",
        json=credential_body(name="x", auth={"type": "basic", "username": "u", "password": "p"}),
    )
    assert wrong_type.status_code == 422
    assert wrong_type.json()["errors"][0]["field"] == "body.auth"

    no_secret = client.post(
        "/api/credentials", json=credential_body(name="y", auth={"type": "token"})
    )
    assert no_secret.status_code == 422
    assert no_secret.json()["errors"] == [
        {"field": "body.auth.token", "message": "required when no secret is stored"}
    ]

    basic = create_credential(
        client,
        name="Wazuh reader",
        kind="wazuh",
        auth={"type": "basic", "username": "reader", "password": "p"},
    )
    assert basic["auth"] == {"type": "basic", "username": "reader", "password_set": True}

    missing = client.get("/api/credentials/missing")
    assert (missing.status_code, missing.json()["code"]) == (404, "credential_not_found")


async def test_update_keeps_omitted_secret_and_delete(client: TestClient) -> None:
    credential = create_credential(client)
    cid = credential["credential_id"]

    renamed = client.put(
        f"/api/credentials/{cid}", json=credential_body(name="Grafana", auth={"type": "token"})
    )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Grafana"
    pid = client.post("/api/projects", json=grafana_project(cid)).json()["project_id"]
    [conn] = await services(client).projects.connections(pid)
    assert isinstance(conn, GrafanaConnection)
    assert conn.token is not None and conn.token.get_secret_value() == TOKEN

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.delete(f"/api/credentials/{cid}").status_code == 204
    assert client.get("/api/credentials").json() == {"items": []}
    assert client.delete(f"/api/credentials/{cid}").status_code == 404


# --- sources using a credential ---------------------------------------------------------------


async def test_sources_share_and_follow_a_rotated_credential(client: TestClient) -> None:
    cid = create_credential(client)["credential_id"]
    shop = client.post("/api/projects", json=grafana_project(cid)).json()
    blog = client.post("/api/projects", json=grafana_project(cid, name="Blog")).json()

    assert shop["sources"][0]["auth"] == {
        "type": "credential",
        "credential_id": cid,
        "name": "Grafana production",
    }
    assert [u["name"] for u in client.get(f"/api/credentials/{cid}").json()["used_by"]] == [
        "Blog",
        "Shop",
    ]

    services(client).health_cache[shop["project_id"]] = (0.0, "v", [])
    rotated = client.put(
        f"/api/credentials/{cid}", json=credential_body(auth={"type": "token", "token": ROTATED})
    )

    assert rotated.status_code == 200
    assert shop["project_id"] not in services(client).health_cache
    for project in (shop, blog):
        [conn] = await services(client).projects.connections(project["project_id"])
        assert conn.token.get_secret_value() == ROTATED

    clone = client.post(
        "/api/projects",
        params={"clone_of": shop["project_id"]},
        json=grafana_project(cid, name="Shop copy"),
    )
    assert clone.json()["sources"][0]["auth"]["credential_id"] == cid


def test_credential_in_use_cannot_be_deleted_or_change_kind(client: TestClient) -> None:
    cid = create_credential(client)["credential_id"]
    client.post("/api/projects", json=grafana_project(cid))

    deleted = client.delete(f"/api/credentials/{cid}")

    assert (deleted.status_code, deleted.json()["code"]) == (409, "credential_in_use")
    assert "Shop" in deleted.json()["detail"]

    moved = client.put(f"/api/credentials/{cid}", json=credential_body(kind="cloudflare"))

    assert (moved.status_code, moved.json()["code"]) == (409, "credential_in_use")


def test_source_rejects_missing_or_foreign_credential(client: TestClient) -> None:
    cid = create_credential(client)["credential_id"]
    cloudflare = {
        "name": "Edge",
        "sources": [
            {
                "kind": "cloudflare",
                "zone_id": ZONE,
                "auth": {"type": "credential", "credential_id": cid},
            }
        ],
    }

    foreign = client.post("/api/projects", json=cloudflare)

    assert foreign.status_code == 422
    assert foreign.json()["errors"] == [
        {
            "field": "body.sources.0.auth.credential_id",
            "message": "a grafana credential cannot be used by a cloudflare source",
        }
    ]

    missing = client.post("/api/projects", json=grafana_project("nope"))

    assert missing.json()["errors"][0]["message"] == "no such credential"


async def test_draft_connection_resolves_credential(client: TestClient) -> None:
    cid = create_credential(
        client,
        name="Wazuh reader",
        kind="wazuh",
        auth={"type": "basic", "username": "reader", "password": "pw"},
    )["credential_id"]
    draft = {
        "source": {
            "kind": "wazuh",
            "api_url": "synthetic://healthy",
            "agents": ["web-1"],
            "auth": {"type": "credential", "credential_id": cid},
        }
    }

    tested = client.post("/api/projects/test-connection", json=draft)

    assert tested.status_code == 200 and tested.json()["reachable"]

    conn = await services(client).projects.resolve_connection(
        WazuhSourceInput.model_validate(draft["source"])
    )
    assert isinstance(conn, WazuhConnection)
    assert conn.username == "reader" and conn.password is not None
    assert conn.password.get_secret_value() == "pw"


def test_unreadable_credential_is_reported(tmp_path: Path) -> None:
    with make_client(tmp_path, secret_key=Fernet.generate_key().decode()) as c:
        cid = create_credential(c)["credential_id"]
        pid = c.post("/api/projects", json=grafana_project(cid)).json()["project_id"]

    with make_client(tmp_path, secret_key=Fernet.generate_key().decode()) as c:
        assert c.get(f"/api/credentials/{cid}").json()["readable"] is False
        assert c.get(f"/api/projects/{pid}").json()["credentials_readable"] is False


def test_demo_projects_share_a_grafana_credential(tmp_path: Path) -> None:
    with make_client(tmp_path, demo_projects=True) as c:
        [credential] = c.get("/api/credentials").json()["items"]
        projects = c.get("/api/projects").json()["items"]

    assert credential["kind"] == "grafana"
    assert len(credential["used_by"]) == len(projects) > 1


# --- moving existing inline secrets (migrations 0007/0008 + startup import) -------------------

NOW = "2026-10-01T00:00:00Z"


def seed_inline(
    tmp_path: Path,
    key: bytes,
    sources: list[tuple[str, str, dict[str, Any], dict[str, str] | None]],
) -> None:
    """Projects with sources that store their own secret, as saved before credentials-only.

    Each entry is (project name, kind, config with ``auth``, secrets or None).
    """
    with make_client(tmp_path, secret_key=key.decode()):
        pass  # migrate an empty database to head
    box = SecretBox(key)
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        for i, (name, kind, config, secrets) in enumerate(sources):
            pid = f"p{i}"
            db.execute(
                "INSERT INTO projects (project_id, name, matchers, created_at, updated_at)"
                " VALUES (?, ?, '[]', ?, ?)",
                (pid, name, NOW, NOW),
            )
            db.execute(
                "INSERT INTO project_sources (project_id, kind, config, secrets, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (pid, kind, json.dumps(config), box.encrypt(secrets) if secrets else None, NOW),
            )
        db.execute(
            "INSERT OR REPLACE INTO app_meta (key, value)"
            " VALUES ('shared_credentials_import', 'pending')"
        )


def grafana_config(url: str = "https://grafana.example.com") -> dict[str, Any]:
    return {"api_url": url, "folders": ["Shop"], "auth": {"type": "token"}}


def wazuh_config(username: str) -> dict[str, Any]:
    return {
        "api_url": "https://indexer:9200",
        "agents": ["web-1"],
        "auth": {"type": "basic", "username": username},
    }


async def test_startup_moves_inline_secrets_into_credentials(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    seed_inline(
        tmp_path,
        key,
        [
            ("Shop", "grafana", grafana_config(), {"token": TOKEN}),
            ("Blog", "grafana", grafana_config(), {"token": TOKEN}),
            ("Other", "grafana", grafana_config(), {"token": ROTATED}),
            (
                "Edge",
                "cloudflare",
                {
                    "zone_id": ZONE,
                    "hostnames": [],
                    "api_url": "https://cf",
                    "auth": {"type": "token"},
                },
                {"api_token": "cf"},
            ),
            ("Hosts A", "wazuh", wazuh_config("reader"), {"password": "pw"}),
            ("Hosts B", "wazuh", wazuh_config("reader"), {"password": "pw"}),
            ("Open", "wazuh", {**wazuh_config("x"), "auth": {"type": "none"}}, None),
        ],
    )

    with make_client(tmp_path, secret_key=key.decode()) as c:
        moved = {cr["name"]: cr for cr in c.get("/api/credentials").json()["items"]}
        projects = {p["name"]: p for p in c.get("/api/projects").json()["items"]}
        shop = await services(c).projects.connections("p0")
        edge = await services(c).projects.connections("p3")

    assert sorted(moved) == [
        "Cloudflare token",
        "Grafana token (grafana.example.com)",
        "Grafana token (grafana.example.com) 2",
        "Wazuh reader@indexer",
    ]
    shared = next(
        cr for cr in moved.values() if len(cr["used_by"]) == 2 and cr["kind"] == "grafana"
    )
    assert [u["name"] for u in shared["used_by"]] == ["Blog", "Shop"]
    assert shared["description"] == "Moved on upgrade from Blog, Shop."
    assert projects["Shop"]["sources"][0]["auth"]["credential_id"] == shared["credential_id"]
    assert projects["Other"]["sources"][0]["auth"]["type"] == "credential"
    assert projects["Open"]["sources"][0]["auth"] == {"type": "none"}
    assert isinstance(shop[0], GrafanaConnection) and shop[0].token is not None
    assert shop[0].token.get_secret_value() == TOKEN
    assert isinstance(edge[0], CloudflareConnection) and edge[0].api_token is not None
    assert edge[0].api_token.get_secret_value() == "cf"
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        inline = db.execute("SELECT COUNT(*) FROM project_sources WHERE secrets IS NOT NULL")
        assert inline.fetchone()[0] == 0

    # The import runs once.
    with make_client(tmp_path, secret_key=key.decode()) as c:
        assert len(c.get("/api/credentials").json()["items"]) == 4


def test_unreadable_inline_secrets_move_as_they_are(tmp_path: Path) -> None:
    seed_inline(
        tmp_path,
        Fernet.generate_key(),
        [
            ("Shop", "grafana", grafana_config(), {"token": TOKEN}),
            ("Blog", "grafana", grafana_config(), {"token": TOKEN}),
        ],
    )

    with make_client(tmp_path, secret_key=Fernet.generate_key().decode()) as c:
        moved = c.get("/api/credentials").json()["items"]
        projects = c.get("/api/projects").json()["items"]

    assert len(moved) == 2  # undecryptable secrets cannot be compared, so each gets its own
    assert [cr["readable"] for cr in moved] == [False, False]
    assert [p["credentials_readable"] for p in projects] == [False, False]


def test_migration_marks_import_pending_only_with_inline_secrets(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "fresh.sqlite3")
    migrate(engine)

    with engine.connect() as conn:
        flag = conn.exec_driver_sql(
            "SELECT value FROM app_meta WHERE key = 'shared_credentials_import'"
        ).first()

    assert flag is None


# --- migration 0006 ---------------------------------------------------------------------------


async def test_migrations_move_secrets_into_credentials(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    box = SecretBox(key)
    engine = make_engine(tmp_path / "assistant.sqlite3")
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0005")
    legacy = {
        "cloudflare": ({"zone_id": ZONE, "hostnames": [], "api_url": "https://cf"}, "api_token"),
        "sentry": ({"organization": "acme", "project": "web", "api_url": "https://s"}, None),
        "wazuh": (
            {
                "api_url": "https://indexer:9200",
                "index_pattern": "wazuh-alerts-4.x-*",
                "agents": ["web-1"],
                "username": "reader",
            },
            "password",
        ),
    }
    now = "2026-10-01T00:00:00Z"
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO projects (project_id, name, matchers, created_at, updated_at)"
            " VALUES ('p1', 'Shop', '[]', ?, ?)",
            (now, now),
        )
        for kind, (cfg, secret_key) in legacy.items():
            secrets = box.encrypt({secret_key: "legacy"}) if secret_key else None
            conn.exec_driver_sql(
                "INSERT INTO project_sources (project_id, kind, config, secrets, updated_at)"
                " VALUES ('p1', ?, ?, ?, ?)",
                (kind, json.dumps(cfg), secrets, now),
            )
    engine.dispose()

    with make_client(tmp_path, secret_key=key.decode()) as c:
        sources = {s["kind"]: s for s in c.get("/api/projects/p1").json()["sources"]}
        moved = {cr["kind"]: cr for cr in c.get("/api/credentials").json()["items"]}
        connections = await services(c).projects.connections("p1")

    assert sources["cloudflare"]["auth"]["credential_id"] == moved["cloudflare"]["credential_id"]
    assert moved["cloudflare"]["auth"] == {"type": "token", "token_set": True}
    assert sources["sentry"]["auth"] == {"type": "none"}
    assert sources["sentry"]["projects"] == ["web"]
    assert moved["wazuh"]["auth"] == {
        "type": "basic",
        "username": "reader",
        "password_set": True,
    }
    by_type = {type(conn): conn for conn in connections}
    cloudflare = by_type[CloudflareConnection]
    wazuh = by_type[WazuhConnection]
    assert isinstance(cloudflare, CloudflareConnection) and isinstance(wazuh, WazuhConnection)
    assert cloudflare.api_token is not None
    assert cloudflare.api_token.get_secret_value() == "legacy"
    assert wazuh.password is not None and wazuh.password.get_secret_value() == "legacy"

    migrate(engine)  # idempotent at head
