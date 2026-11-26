"""Source connections (#9, #12): sources are closed after use, resolve credentials alike, and
every API operation is bounded end to end."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domain.custom_rules import CustomRule
from app.domain.projects import (
    ConnectionTestRequest,
    CustomRulePreviewRequest,
    Project,
    PrometheusConnection,
)
from app.main import create_app
from app.settings import load_settings
from app.sources import connections as connections_module
from app.sources.connections import SourceConnections
from app.sources.prometheus.connect import connect_prometheus
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


# --- bounds (#12) -----------------------------------------------------------------------------

BOUND = 0.05
NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
RULE = CustomRule.model_validate(
    {"slug": "queue_depth", "title": "Queue depth", "query": "max(queue_messages)", "unit": "count"}
)


async def hang(request: httpx.Request) -> httpx.Response:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


@pytest.fixture
def short_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections_module, "PROBE_TIMEOUT_SECONDS", BOUND)
    monkeypatch.setattr(connections_module, "PREVIEW_TIMEOUT_SECONDS", BOUND)


def hanging_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening a Prometheus source never finishes."""

    @asynccontextmanager
    async def connect(conn: PrometheusConnection, transport: Any) -> AsyncGenerator[Any]:
        await asyncio.Event().wait()
        yield None

    monkeypatch.setattr(connections_module, "connect_prometheus", connect)


def preview_request(source: dict[str, Any]) -> CustomRulePreviewRequest:
    return CustomRulePreviewRequest.model_validate(
        {"source": source, "matchers": MATCHERS, "rule": RULE.model_dump(mode="json")}
    )


@pytest.mark.usefixtures("short_bounds")
@pytest.mark.parametrize("kind", KINDS)
async def test_hanging_probe_times_out_and_closes_its_source(client: TestClient, kind: str) -> None:
    source = source_json(client, kind)
    project = await saved_project(client, source)
    transport = RecordingTransport(hang)
    sources = connections(client, transport)
    draft = ConnectionTestRequest.model_validate({"source": source, **matchers(source)})

    [health] = await sources.health(project)
    tested = await sources.test_draft(draft.source, draft.matchers)

    for result in (health, tested):
        assert result.kind.value == kind
        assert (result.reachable, result.auth_ok) == (False, None)
        assert result.message is not None and result.message.startswith("No answer within")
    assert transport.closed == 2


@pytest.mark.usefixtures("short_bounds")
async def test_hanging_open_times_out(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    source = source_json(client, "prometheus")
    project = await saved_project(client, source)
    hanging_open(monkeypatch)
    sources = connections(client, RecordingTransport(denied))
    preview = preview_request(source)

    [health] = await sources.health(project)
    rule = await sources.preview_rule(preview.source, preview.rule, preview.matchers, NOW)

    assert (health.reachable, health.auth_ok) == (False, None)
    assert health.message is not None and health.message.startswith("No answer within")
    assert (rule.ok, rule.query, rule.series_count) == (False, None, 0)
    assert rule.message is not None and rule.message.startswith("No answer within")


@pytest.mark.usefixtures("short_bounds")
async def test_hanging_close_times_out(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    project = await saved_project(client, source_json(client, "prometheus"))

    @asynccontextmanager
    async def connect(conn: PrometheusConnection, transport: Any) -> AsyncGenerator[Any]:
        async with connect_prometheus(conn, transport) as prometheus:
            yield prometheus
            await asyncio.Event().wait()

    monkeypatch.setattr(connections_module, "connect_prometheus", connect)
    sources = connections(client, RecordingTransport(denied))

    [health] = await sources.health(project)

    assert (health.reachable, health.auth_ok) == (False, None)


@pytest.mark.usefixtures("short_bounds")
async def test_hanging_preview_query_times_out(client: TestClient) -> None:
    transport = RecordingTransport(hang)
    sources = connections(client, transport)
    preview = preview_request(source_json(client, "prometheus"))

    rule = await sources.preview_rule(preview.source, preview.rule, preview.matchers, NOW)

    assert (rule.ok, rule.query, rule.series, rule.step_seconds) == (False, None, [], 300)
    assert rule.start == datetime(2026, 11, 25, 12, tzinfo=UTC)
    assert rule.message is not None and rule.message.startswith("No answer within")
    assert transport.closed == 1


async def test_health_probes_sources_concurrently(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await saved_project_with(
        client, [source_json(client, "prometheus"), source_json(client, "cloudflare")]
    )
    started: list[str] = []
    both = asyncio.Event()

    async def record(request: httpx.Request) -> httpx.Response:
        started.append(request.url.host)
        if len({*started}) == 2:
            both.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(connections_module, "PROBE_TIMEOUT_SECONDS", 60)
    probe = asyncio.create_task(connections(client, RecordingTransport(record)).health(project))

    await asyncio.wait_for(both.wait(), 5)

    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe


async def test_health_keeps_source_order(client: TestClient) -> None:
    project = await saved_project_with(
        client, [source_json(client, "sentry"), source_json(client, "prometheus")]
    )

    health = await connections(client, RecordingTransport(denied)).health(project)

    stored = [conn.kind for conn in await services(client).projects.connections(project.project_id)]
    assert [h.kind for h in health] == stored
    assert len(stored) == 2


@pytest.mark.usefixtures("short_bounds")
def test_hanging_source_fails_the_routes_within_the_bound(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_json(client, "prometheus")
    hanging_open(monkeypatch)

    tested = client.post(
        "/api/projects/test-connection", json={"source": source, "matchers": MATCHERS}
    )
    previewed = client.post(
        "/api/projects/custom-rules/preview",
        json={"source": source, "matchers": MATCHERS, "rule": RULE.model_dump(mode="json")},
    )

    assert [tested.status_code, previewed.status_code] == [200, 200]
    assert tested.json()["reachable"] is False
    assert tested.json()["message"].startswith("No answer within")
    assert previewed.json()["ok"] is False
    assert previewed.json()["message"].startswith("No answer within")
    assert SECRET not in tested.text + previewed.text


async def saved_project_with(client: TestClient, sources: list[dict[str, Any]]) -> Project:
    res = client.post(
        "/api/projects", json={"name": "Shop", "sources": sources, "matchers": MATCHERS}
    )
    assert res.status_code == 201, res.text

    project: Project | None = await services(client).projects.get(res.json()["project_id"])
    assert project is not None
    return project
