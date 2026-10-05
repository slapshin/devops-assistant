"""Project model, encrypted storage, CRUD API, and connection test (T011)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.domain.common import LabelMatcher, Scope
from app.domain.projects import PrometheusConnection
from app.main import create_app
from app.metrics.client import PrometheusClient
from app.metrics.probe import probe
from app.metrics.source import PrometheusMetricsSource
from app.settings import load_settings
from app.storage.secrets import SecretBox, SecretsUnreadable

TOKEN = "s3cr3t-token-value"
PASSWORD = "pa55word-value"


def make_client(tmp_path: Path, **settings: Any) -> TestClient:
    config = {
        "_env_file": None,
        "data_dir": tmp_path,
        "ai_provider": "fake",
        **settings,
    }
    return TestClient(create_app(load_settings(**config)))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Shop / prod",
        "description": "  Web shop  ",
        "matchers": [{"name": "project", "value": "shop"}, {"name": "env", "value": "prod"}],
        "sources": [
            {
                "kind": "prometheus",
                "url": "http://vm.example:8428/select/0/prometheus/",
                "auth": {"type": "bearer", "token": TOKEN},
            }
        ],
        **overrides,
    }


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    res = client.post("/api/projects", json=body(**overrides))
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def raw_secrets(tmp_path: Path) -> list[bytes]:
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        return [r[0] for r in db.execute("SELECT secrets FROM project_sources") if r[0]]


# --- CRUD and persistence ---------------------------------------------------------------------


def test_crud_round_trip_and_persistence(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        project = create(c)
        assert project["name"] == "Shop / prod"
        assert project["description"] == "Web shop"
        assert [m["name"] for m in project["matchers"]] == ["env", "project"]  # sorted
        assert project["sources"] == [
            {
                "kind": "prometheus",
                "url": "http://vm.example:8428/select/0/prometheus",
                "tls_verify": True,
                "auth": {"type": "bearer", "token_set": True},
            }
        ]
        assert project["credentials_readable"] is True

    with make_client(tmp_path) as c:  # restart
        pid = project["project_id"]
        summary = {**project, "latest_analysis": None, "active_analysis": None, "report_count": 0}
        assert c.get("/api/projects").json()["items"] == [summary]
        assert c.get(f"/api/projects/{pid}").json() == summary

        res = c.put(f"/api/projects/{pid}", json=body(name="Shop", sources=[]))
        assert res.status_code == 200
        assert res.json()["name"] == "Shop"
        assert res.json()["sources"] == []
        assert res.json()["updated_at"] >= project["updated_at"]

        assert c.delete(f"/api/projects/{pid}").status_code == 204
        assert c.get(f"/api/projects/{pid}").json()["code"] == "project_not_found"
        assert c.delete(f"/api/projects/{pid}").status_code == 404
        assert c.put(f"/api/projects/{pid}", json=body()).status_code == 404


def test_names_are_unique_case_insensitively(client: TestClient) -> None:
    first = create(client)
    res = client.post("/api/projects", json=body(name="  shop / PROD "))
    assert res.status_code == 409
    assert res.json()["code"] == "project_name_taken"

    second = create(client, name="Other")
    res = client.put(f"/api/projects/{second['project_id']}", json=body(name="SHOP / prod"))
    assert res.json()["code"] == "project_name_taken"
    # Renaming a project to its own name in a different case is fine.
    res = client.put(f"/api/projects/{first['project_id']}", json=body(name="SHOP / PROD"))
    assert res.status_code == 200


# --- secrets ----------------------------------------------------------------------------------


def test_secrets_are_encrypted_and_never_returned(tmp_path: Path, client: TestClient) -> None:
    project = create(client)
    other = create(
        client,
        name="basic",
        sources=[
            {
                "kind": "prometheus",
                "url": "https://vm.example",
                "auth": {"type": "basic", "username": "reader", "password": PASSWORD},
            }
        ],
    )
    assert other["sources"][0]["auth"] == {
        "type": "basic",
        "username": "reader",
        "password_set": True,
    }

    stored = raw_secrets(tmp_path)
    assert len(stored) == 2
    assert not any(TOKEN.encode() in s or PASSWORD.encode() in s for s in stored)
    key = (tmp_path / "secret.key").read_bytes()
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600
    assert {"token": TOKEN} in [SecretBox(key).decrypt(s) for s in stored]

    for path in ("/api/projects", f"/api/projects/{project['project_id']}"):
        text = client.get(path).text
        assert TOKEN not in text
        assert PASSWORD not in text


async def test_omitted_secret_keeps_stored_value(tmp_path: Path, client: TestClient) -> None:
    project = create(client)
    pid = project["project_id"]
    keep = body(
        sources=[{"kind": "prometheus", "url": "http://vm2.example", "auth": {"type": "bearer"}}]
    )
    assert client.put(f"/api/projects/{pid}", json=keep).status_code == 200

    repo = client.app.state.services.projects  # type: ignore[attr-defined]
    conn = await repo.prometheus_connection(pid)
    assert conn.url == "http://vm2.example"
    assert conn.bearer_token.get_secret_value() == TOKEN

    # Switching auth type requires the new secret.
    switch = body(
        sources=[
            {
                "kind": "prometheus",
                "url": "http://vm2.example",
                "auth": {"type": "basic", "username": "u"},
            }
        ]
    )
    res = client.put(f"/api/projects/{pid}", json=switch)
    assert res.status_code == 422
    assert res.json()["errors"] == [
        {"field": "body.sources.0.auth.password", "message": "required when no secret is stored"}
    ]
    # Creating without a secret is rejected the same way.
    res = client.post("/api/projects", json={**keep, "name": "new"})
    assert res.json()["errors"][0]["field"] == "body.sources.0.auth.token"


async def test_clone_reuses_stored_secrets(client: TestClient) -> None:
    source = create(client)
    clone = body(
        name="Shop / staging",
        sources=[
            {"kind": "prometheus", "url": "http://vm.example:8428", "auth": {"type": "bearer"}}
        ],
    )
    res = client.post("/api/projects", params={"clone_of": source["project_id"]}, json=clone)
    assert res.status_code == 201, res.text

    repo = client.app.state.services.projects  # type: ignore[attr-defined]
    conn = await repo.prometheus_connection(res.json()["project_id"])
    assert conn.bearer_token.get_secret_value() == TOKEN

    # The clone owns its copy: changing the original's secret does not affect it.
    replaced = body(
        sources=[
            {
                "kind": "prometheus",
                "url": "http://vm.example",
                "auth": {"type": "bearer", "token": "x"},
            }
        ]
    )
    assert client.put(f"/api/projects/{source['project_id']}", json=replaced).status_code == 200
    conn = await repo.prometheus_connection(res.json()["project_id"])
    assert conn.bearer_token.get_secret_value() == TOKEN

    res = client.post("/api/projects", params={"clone_of": "missing"}, json=body(name="other"))
    assert res.status_code == 404
    assert res.json()["code"] == "project_not_found"


def test_lost_key_degrades_only_affected_projects(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        secret = create(c)
        plain = create(
            c, name="plain", sources=[{"kind": "prometheus", "url": "http://vm.example"}]
        )

    other_key = Fernet.generate_key().decode()
    with make_client(tmp_path, secret_key=other_key) as c:
        by_id = {p["project_id"]: p for p in c.get("/api/projects").json()["items"]}
        assert by_id[secret["project_id"]]["credentials_readable"] is False
        assert by_id[plain["project_id"]]["credentials_readable"] is True

        res = c.post(
            "/api/projects/test-connection",
            json={
                "project_id": secret["project_id"],
                "matchers": body()["matchers"],
                "source": {"url": "http://vm.example", "auth": {"type": "bearer"}},
            },
        )
        assert res.status_code == 409
        assert res.json()["code"] == "credentials_unreadable"
        assert c.get(f"/api/projects/{secret['project_id']}/health").status_code == 409

        # Re-entering the secret repairs the project.
        res = c.put(f"/api/projects/{secret['project_id']}", json=body())
        assert res.json()["credentials_readable"] is True


def test_invalid_secret_key_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        make_client(tmp_path, secret_key="not-a-fernet-key")


def test_secret_box_rejects_foreign_ciphertext() -> None:
    blob = SecretBox(Fernet.generate_key()).encrypt({"token": "x"})
    with pytest.raises(SecretsUnreadable):
        SecretBox(Fernet.generate_key()).decrypt(blob)


# --- validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"matchers": [{"name": "1bad", "value": "x"}]}, "body.matchers.0.name"),
        ({"matchers": [{"name": "__name__", "value": "up"}]}, "body.matchers.0.name"),
        ({"matchers": [{"name": "env", "value": ""}]}, "body.matchers.0.value"),
        ({"matchers": []}, "body.matchers"),
        (
            {"matchers": [{"name": "env", "value": "a"}, {"name": "env", "value": "b"}]},
            "body.matchers",
        ),
        ({"name": "   "}, "body.name"),
        ({"sources": [{"kind": "prometheus", "url": "localhost:8428"}]}, "body.sources.0.url"),
        (
            {"sources": [{"kind": "prometheus", "url": "http://u:p@vm.example"}]},
            "body.sources.0.url",
        ),
        (
            {"sources": [{"kind": "prometheus", "url": "synthetic://nope"}]},
            "body.sources.0.url",
        ),
    ],
)
def test_validation_errors_name_the_field(
    client: TestClient, overrides: dict[str, Any], field: str
) -> None:
    res = client.post("/api/projects", json=body(**overrides))
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "validation_error"
    assert any(e["field"] == field for e in res.json()["errors"]), res.json()["errors"]


# --- connection test --------------------------------------------------------------------------


def test_connection_test_synthetic_and_health(client: TestClient) -> None:
    res = client.post(
        "/api/projects/test-connection",
        json={"matchers": body()["matchers"], "source": {"url": "synthetic://incident"}},
    )
    assert res.status_code == 200
    assert res.json()["reachable"] is True
    families = {f["family"]: f["status"] for f in res.json()["families"]}
    assert families["cpu"] == "supported" and families["latency"] == "supported"

    project = create(client, sources=[{"kind": "prometheus", "url": "synthetic://healthy"}])
    health = client.get(f"/api/projects/{project['project_id']}/health").json()
    assert health["reachable"] is True
    no_source = create(client, name="empty", sources=[])
    assert client.get(f"/api/projects/{no_source['project_id']}/health").json() is None
    assert client.get("/api/projects/missing/health").status_code == 404


SCOPE = Scope(
    project_id="draft",
    project_name="draft",
    matchers=[LabelMatcher(name="project", value='sh"op')],
)


async def run_probe(handler: Any) -> Any:
    conn = PrometheusConnection(url="http://vm.example", bearer_token=TOKEN)
    client = PrometheusClient.from_connection(conn, transport=httpx.MockTransport(handler))
    return await probe(PrometheusMetricsSource(client), client, SCOPE)


def empty(request: httpx.Request) -> httpx.Response:
    kind = "vector" if request.url.path.endswith("/query") else "matrix"
    return httpx.Response(
        200, json={"status": "success", "data": {"resultType": kind, "result": []}}
    )


async def test_probe_counts_matching_series_and_history() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        query = request.url.params["query"]
        if query == 'count({project="sh\\"op"})':
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [{"metric": {}, "value": [0, "42"]}],
                    },
                },
            )
        if query != 'count(up{project="sh\\"op"})':
            return empty(request)
        start = int(request.url.params["start"])
        values = [[start + 86400, "3"], [start + 2 * 86400, "3"]]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"metric": {}, "values": values}]},
            },
        )

    result = await run_probe(handler)
    assert result.reachable and result.auth_ok
    assert result.matched_series == 42
    assert result.history_days == 29.0
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert seen[0].url.params["query"] == 'count({project="sh\\"op"})'
    assert seen[1].url.params["query"] == 'count(up{project="sh\\"op"})'
    assert {f.family.value for f in result.families} >= {"cpu", "latency"}
    assert all(f.status.value == "unsupported" for f in result.families)


async def test_probe_reports_auth_failure_and_unreachable() -> None:
    result = await run_probe(lambda _: httpx.Response(401))
    assert (result.reachable, result.auth_ok) == (True, False)
    assert TOKEN not in (result.message or "")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await run_probe(refuse)
    assert (result.reachable, result.auth_ok) == (False, None)


async def test_probe_reports_no_matching_series() -> None:
    result = await run_probe(empty)
    assert result.matched_series == 0
    assert result.history_days is None
    assert result.message == "No series currently match these labels."
