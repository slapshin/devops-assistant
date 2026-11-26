"""Source connections (#9): every kind is closed after use and resolves credentials alike."""

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domain.projects import ConnectionTestRequest, Project
from app.main import create_app
from app.settings import load_settings
from app.sources.connections import SourceConnections
from tests.helpers import create_credential, credential_ref, make_scope

SECRET = "s3cr3t-value-for-tests"
TOKEN = {"type": "token", "token": SECRET}
SOURCES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "prometheus": (
        {"kind": "prometheus", "url": "http://vm.example"},
        {"type": "bearer", "token": SECRET},
    ),
    "cloudflare": ({"kind": "cloudflare", "zone_id": "0" * 32}, TOKEN),
    "sentry": (
        {
            "kind": "sentry",
            "organization": "acme",
            "projects": ["web"],
            "api_url": "https://sentry.example",
        },
        TOKEN,
    ),
    "wazuh": (
        {"kind": "wazuh", "api_url": "https://wazuh.example:9200", "agents": ["web-1"]},
        {"type": "basic", "username": "reader", "password": SECRET},
    ),
    "grafana": (
        {"kind": "grafana", "api_url": "https://grafana.example", "folders": ["Shop"]},
        TOKEN,
    ),
}
KINDS = list(SOURCES)
MATCHERS = [{"name": "project", "value": "shop"}]


class RecordingTransport(httpx.MockTransport):
    """MockTransport that counts how often a client using it was closed."""

    def __init__(self, handler: Callable[..., Any]) -> None:
        super().__init__(handler)
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def denied(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "denied"})


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    with TestClient(create_app(settings)) as c:
        yield c


def source_json(client: TestClient, kind: str) -> dict[str, Any]:
    """The kind's real-URL source, authenticated by a new credential of that kind."""
    source, auth = SOURCES[kind]
    credential = create_credential(client, kind, auth)
    return {**source, "auth": credential_ref(credential)}


async def saved_project(client: TestClient, source: dict[str, Any]) -> Project:
    res = client.post(
        "/api/projects", json={"name": "Shop", "sources": [source], **matchers(source)}
    )
    assert res.status_code == 201, res.text

    project: Project | None = await services(client).projects.get(res.json()["project_id"])
    assert project is not None
    return project


def matchers(source: dict[str, Any]) -> dict[str, Any]:
    return {"matchers": MATCHERS} if source["kind"] == "prometheus" else {}


def services(client: TestClient) -> Any:
    return client.app.state.services  # type: ignore[attr-defined]


def connections(client: TestClient, transport: httpx.AsyncBaseTransport) -> SourceConnections:
    return SourceConnections(services(client).projects, transport=transport)


@pytest.mark.parametrize("kind", KINDS)
async def test_analysis_sources_are_closed_after_use_and_on_error(
    client: TestClient, kind: str
) -> None:
    project = await saved_project(client, source_json(client, kind))
    transport = RecordingTransport(denied)
    sources = connections(client, transport)
    scope = make_scope(project_id=project.project_id)

    async with sources.open(scope) as opened:
        assert [o.kind.value for o in opened] == [kind]
        assert transport.closed == 0

    assert transport.closed == 1

    with pytest.raises(RuntimeError):
        async with sources.open(scope):
            raise RuntimeError("analysis failed")

    assert transport.closed == 2


@pytest.mark.parametrize("kind", KINDS)
async def test_probes_close_their_source_on_failure(client: TestClient, kind: str) -> None:
    source = source_json(client, kind)
    project = await saved_project(client, source)
    transport = RecordingTransport(denied)
    sources = connections(client, transport)
    draft = ConnectionTestRequest.model_validate({"source": source, **matchers(source)})

    [health] = await sources.health(project)
    tested = await sources.test_draft(draft.source, draft.matchers)

    assert health.kind.value == tested.kind.value == kind
    assert (health.reachable, health.auth_ok) == (tested.reachable, tested.auth_ok) == (True, False)
    assert transport.closed == 2


@pytest.mark.parametrize("kind", KINDS)
async def test_cancelled_probe_closes_its_source(client: TestClient, kind: str) -> None:
    project = await saved_project(client, source_json(client, kind))
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    transport = RecordingTransport(hang)
    probe = asyncio.create_task(connections(client, transport).health(project))
    await started.wait()

    probe.cancel()

    with pytest.raises(asyncio.CancelledError):
        await probe
    assert transport.closed == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_draft_and_saved_probes_resolve_the_same_credential(
    client: TestClient, kind: str
) -> None:
    source = source_json(client, kind)
    project = await saved_project(client, source)
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return denied(request)

    sources = connections(client, RecordingTransport(record))
    draft = ConnectionTestRequest.model_validate({"source": source, **matchers(source)})

    await sources.health(project)
    saved = {r.headers.get("Authorization") for r in seen}
    seen.clear()
    await sources.test_draft(draft.source, draft.matchers)
    drafted = {r.headers.get("Authorization") for r in seen}

    assert saved == drafted
    assert None not in saved
