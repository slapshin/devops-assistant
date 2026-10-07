"""Wazuh source (T018): indexer client, collection, detection, and project API."""

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.ai.providers import FakeExplanationProvider
from app.analysis.engine import RobustDetector
from app.domain.common import EntityKind, Scope, SignalFamily, SourceKind
from app.domain.detector_config import DetectorConfig
from app.domain.interfaces import CancellationToken, OpenedSource, SourceError, SourceErrorKind
from app.domain.jobs import StageProgress
from app.domain.metrics import CapabilityStatus
from app.domain.projects import WazuhConnection, WazuhLabel, WazuhSourceInput
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows, ReportState
from app.main import create_app
from app.service import AnalysisPipeline
from app.settings import load_settings
from app.sources.base import HISTORY_DAYS, ClientLimits
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from app.sources.wazuh.api import AgentInfo, AgentList, Chunk, GroupMembers
from app.sources.wazuh.catalog import BUCKET_BUDGET, FILTERED, MAX_AGENTS, chunk_seconds
from app.sources.wazuh.client import WazuhClient
from app.sources.wazuh.connect import connect_wazuh, probe_wazuh
from app.sources.wazuh.source import WazuhMetricsSource
from app.sources.wazuh.synthetic import SyntheticWazuhApi
from tests.helpers import StaticSources, make_scope

INDEX = "wazuh-alerts-4.x-*"
PASSWORD = "wazuh-s3cr3t-password"
END = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
SCOPE = Scope(project_id="p-wazuh", project_name="Shop hosts", matchers=[])
Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, api_url: str = "https://indexer:9200") -> WazuhClient:
    conn = WazuhConnection(
        api_url=api_url,
        agents=["web-1"],
        labels=[WazuhLabel(key="project", value='shop "eu"')],
        username="reader",
        password=SecretStr(PASSWORD),
    )
    return WazuhClient(
        conn,
        limits=ClientLimits(timeout_seconds=5, retries=1),
        transport=httpx.MockTransport(handler),
    )


def ok(aggregations: dict[str, Any], **extra: Any) -> httpx.Response:
    body = {
        "took": 3,
        "timed_out": False,
        "_shards": {"total": 3, "successful": 3, "skipped": 0, "failed": 0},
        "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []},
        "aggregations": aggregations,
        **extra,
    }
    return httpx.Response(200, json=body)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class Progress:
    async def update(self, progress: StageProgress) -> None:
        pass


# --- source input ---------------------------------------------------------------------------


def test_source_input_normalises_and_validates() -> None:
    source = WazuhSourceInput.model_validate(
        {
            "api_url": "https://indexer:9200/",
            "agents": [" web-2", "web-1", "web-2"],
            "labels": [{"key": "project", "value": "shop"}, {"key": "env", "value": "prod"}],
            "username": " ",
        }
    )
    assert source.api_url == "https://indexer:9200"
    assert source.agents == ["web-1", "web-2"]
    assert [label.key for label in source.labels] == ["env", "project"]
    assert (source.index_pattern, source.username, source.tls_verify) == (INDEX, None, True)

    with pytest.raises(ValidationError, match="at least one is required"):
        WazuhSourceInput(api_url="https://indexer:9200")
    with pytest.raises(ValidationError, match="invalid agent names"):
        WazuhSourceInput(api_url="https://indexer:9200", agents=["web 1"])
    with pytest.raises(ValidationError):
        WazuhSourceInput(api_url="https://i:9200", agents=[f"a{i}" for i in range(51)])
    with pytest.raises(ValidationError, match="duplicate label keys"):
        WazuhSourceInput.model_validate(
            {
                "api_url": "https://indexer:9200",
                "labels": [{"key": "env", "value": "a"}, {"key": "env", "value": "b"}],
            }
        )
    for pattern in ("wazuh-*,-secret", "remote:wazuh-*", "-wazuh", "wazuh alerts"):
        with pytest.raises(ValidationError, match="index pattern"):
            WazuhSourceInput(api_url="https://indexer:9200", agents=["a"], index_pattern=pattern)
    with pytest.raises(ValidationError):
        WazuhSourceInput.model_validate(
            {"api_url": "https://indexer:9200", "labels": [{"key": "a b", "value": "x"}]}
        )


def test_groups_alone_select_agents_and_are_validated() -> None:
    source = WazuhSourceInput(api_url="https://indexer:9200", groups=["web", " db", "web"])
    assert (source.groups, source.monitoring_index_pattern) == (["db", "web"], "wazuh-monitoring-*")
    for bad in (["web servers"], [".."], ["a/b"]):
        with pytest.raises(ValidationError, match="invalid group names"):
            WazuhSourceInput(api_url="https://indexer:9200", groups=bad)
    with pytest.raises(ValidationError):
        WazuhSourceInput(api_url="https://indexer:9200", groups=[f"g{i}" for i in range(11)])
    with pytest.raises(ValidationError, match="index pattern"):
        WazuhSourceInput(
            api_url="https://indexer:9200", groups=["web"], monitoring_index_pattern="a,b"
        )


# --- client ---------------------------------------------------------------------------------


async def test_discovery_request_is_dsl_scoped_to_agents_and_labels() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(
            {
                "agents": {
                    "buckets": [
                        {"key": "web-1", "doc_count": 12, "first": {"value": ms(END) - 3600_000}}
                    ]
                }
            }
        )

    client = make_client(handler)
    found = await client.agents(["web-1"], int(END.timestamp()) - 86400, int(END.timestamp()))
    await client.aclose()

    assert found == AgentList([AgentInfo("web-1", 12, int(END.timestamp()) - 3600)])
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/wazuh-alerts-4.x-*/_search"
    assert request.headers["authorization"].startswith("Basic ")
    body = json.loads(request.content)
    filters = body["query"]["bool"]["filter"]
    assert {"terms": {"agent.name": ["web-1"]}} in filters
    assert {"term": {"agent.labels.project": 'shop "eu"'}} in filters, "values stay data"
    assert "query_string" not in request.content.decode()
    assert body["size"] == 0
    assert body["aggs"]["agents"]["terms"]["size"] == MAX_AGENTS + 1


async def test_group_members_come_from_the_monitoring_index() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        agents = [{"key": "web-1", "doc_count": 96}, {"key": "web-2", "doc_count": 96}]
        return ok({"groups": {"buckets": [{"key": "web", "agents": {"buckets": agents}}]}})

    client = make_client(handler)
    members = await client.group_members(["db", "web"], 0, 86400)
    await client.aclose()

    assert members == GroupMembers({"db": [], "web": ["web-1", "web-2"]})
    assert members.names() == ["web-1", "web-2"]
    request = seen[0]
    assert request.url.path == "/wazuh-monitoring-*/_search"
    body = json.loads(request.content)
    assert {"terms": {"group": ["db", "web"]}} in body["query"]["bool"]["filter"]
    assert body["aggs"]["groups"]["terms"]["include"] == ["db", "web"]


async def test_missing_monitoring_index_explains_dashboard_monitoring() -> None:
    client = make_client(lambda request: ok({}, _shards={"total": 0, "failed": 0}))
    with pytest.raises(SourceError, match=r"wazuh\.monitoring\.enabled"):
        await client.group_members(["web"], 0, 300)
    await client.aclose()


async def test_an_empty_selection_never_searches_every_agent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    client = make_client(handler)
    assert await client.agents([], 0, 300) == AgentList([])
    await client.aclose()


async def test_series_parses_counts_per_agent_and_signal() -> None:
    bucket = ms(END - timedelta(minutes=5))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        histogram = body["aggs"]["agents"]["aggs"]["time"]["date_histogram"]
        assert histogram == {"field": "timestamp", "fixed_interval": "5m"}
        assert set(
            body["aggs"]["agents"]["aggs"]["time"]["aggs"]["signals"]["filters"]["filters"]
        ) == {s.signal for s in FILTERED}
        return ok(
            {
                "agents": {
                    "buckets": [
                        {
                            "key": "web-1",
                            "doc_count": 9,
                            "time": {
                                "buckets": [
                                    {
                                        "key": bucket,
                                        "doc_count": 9,
                                        "signals": {
                                            "buckets": {
                                                "wazuh_high_alerts": {"doc_count": 1},
                                                "wazuh_auth_failures": {"doc_count": 7},
                                                "wazuh_fim_changes": {"doc_count": 0},
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        )

    client = make_client(handler)
    end = int(END.timestamp())
    chunk = await client.series(["web-1"], end - 3600, end)
    await client.aclose()
    at = bucket // 1000
    assert chunk.values["wazuh_alerts"] == {"web-1": {at: 9.0}}
    assert chunk.values["wazuh_auth_failures"] == {"web-1": {at: 7.0}}
    assert chunk.values["wazuh_high_alerts"] == {"web-1": {at: 1.0}}
    assert chunk.values["wazuh_fim_changes"] == {"web-1": {at: 0.0}}


@pytest.mark.parametrize(
    ("response", "kind", "text"),
    [
        (httpx.Response(401, text="Unauthorized"), SourceErrorKind.AUTH, "user or password"),
        (
            httpx.Response(
                403,
                json={"error": {"type": "security_exception", "reason": "no permissions"}},
            ),
            SourceErrorKind.AUTH,
            "needs read",
        ),
        (
            httpx.Response(
                404,
                json={"error": {"type": "index_not_found_exception", "reason": "no such index"}},
            ),
            SourceErrorKind.BAD_QUERY,
            "check the index pattern",
        ),
        (
            httpx.Response(302, headers={"location": "https://login.example/"}),
            SourceErrorKind.BAD_QUERY,
            "redirect",
        ),
        (
            httpx.Response(
                400,
                json={
                    "error": {
                        "root_cause": [
                            {"type": "too_many_buckets_exception", "reason": "max 65535"}
                        ],
                        "type": "search_phase_execution_exception",
                    }
                },
            ),
            SourceErrorKind.TOO_LARGE,
            "max 65535",
        ),
        (ok({"agents": {"buckets": []}}, timed_out=True), SourceErrorKind.TIMEOUT, "timed out"),
        (
            ok({"agents": {"buckets": []}}, _shards={"total": 4, "failed": 1}),
            SourceErrorKind.SERVER_ERROR,
            "1 of 4 shards failed",
        ),
        (
            ok({"agents": {"buckets": []}}, _shards={"total": 0, "failed": 0}),
            SourceErrorKind.BAD_QUERY,
            "matches no indices",
        ),
        (httpx.Response(502), SourceErrorKind.SERVER_ERROR, "502"),
    ],
)
async def test_errors_are_classified_without_the_password(
    response: httpx.Response, kind: SourceErrorKind, text: str
) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(SourceError) as caught:
        await client.agents(None, 0, 300)
    await client.aclose()
    assert caught.value.kind is kind
    assert text in caught.value.message
    assert PASSWORD not in caught.value.message


async def test_url_prefix_is_kept_and_untrusted_certificates_are_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://proxy.internal/indexer/wazuh-alerts-4.x-*/")
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")

    client = make_client(handler, "https://proxy.internal/indexer")
    with pytest.raises(SourceError, match="turn off TLS verification"):
        await client.agents(None, 0, 300)
    await client.aclose()


async def test_server_errors_are_retried_once_and_version_is_best_effort() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "GET":
            return httpx.Response(403)
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return ok({"agents": {"buckets": []}})

    client = make_client(handler)
    assert (await client.agents(None, 0, 300)).agents == []
    assert calls == 2
    assert await client.version() is None
    await client.aclose()


def test_chunks_stay_within_the_bucket_budget() -> None:
    for agents in (1, 3, 10, 50):
        width = chunk_seconds(agents, 300)
        assert width % 300 == 0
        assert agents * (width // 300) * (1 + len(FILTERED)) <= BUCKET_BUDGET
    assert chunk_seconds(1, 300) == 7 * 86400
    assert chunk_seconds(50, 300) == 16 * 3600


# --- collection -----------------------------------------------------------------------------


class RecordingApi(SyntheticWazuhApi):
    def __init__(self, *args: Any, fail: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[list[str], int, int]] = []
        self.fail = fail

    async def series(self, agents: Any, start: int, end: int) -> Chunk:
        self.calls.append((list(agents), start, end))
        if self.fail and len(self.calls) == 1:
            raise SourceError(SourceErrorKind.TIMEOUT, "no response within 30 s")
        return await super().series(agents, start, end)


async def collect(source: WazuhMetricsSource) -> tuple[Any, list[Any]]:
    windows = AnalysisWindows.for_end(END)
    caps = await source.capabilities(SCOPE, windows)
    result = await source.collect(SCOPE, windows, caps, Progress(), CancellationToken())
    return result, caps


async def test_each_agent_is_its_own_entity_with_all_signals() -> None:
    api = RecordingApi("healthy", INDEX, ["web-1", "db-1"])
    result, caps = await collect(WazuhMetricsSource(api, INDEX, ["web-1", "db-1"]))
    assert {c.status for c in caps} == {CapabilityStatus.SUPPORTED}
    assert {c.family for c in caps} == {SignalFamily.HOST_SECURITY, SignalFamily.FILE_INTEGRITY}
    entities = {s.entity.display_name for s in result.series}
    assert entities == {"web-1", "db-1"}
    assert {s.entity.kind for s in result.series} == {EntityKind.AGENT}
    assert len(result.series) == 2 * 4
    assert all(s.coverage == 1.0 for s in result.series)
    assert all(calls[0] == ["db-1", "web-1"] for calls in api.calls)
    assert result.exclusions == []


async def test_history_starts_at_the_first_alert() -> None:
    api = RecordingApi("short-history", INDEX, ["web-1"])
    result, caps = await collect(WazuhMetricsSource(api, INDEX, ["web-1"]))
    assert caps[0].history_days is not None and 4.9 < caps[0].history_days <= 5.0
    series = result.series[0]
    assert series.values[0] is None, "nothing before the first alert is reported as quiet"
    assert series.values[-1] is not None
    assert 0.17 < series.coverage < 0.19  # 5 of 28 days


async def test_failed_chunks_are_unknown_and_disclosed() -> None:
    api = RecordingApi("healthy", INDEX, ["web-1"], fail=True)
    result, _ = await collect(WazuhMetricsSource(api, INDEX, ["web-1"]))
    codes = {(e.code, e.family) for e in result.exclusions}
    assert codes == {
        ("query_timeout", SignalFamily.HOST_SECURITY),
        ("query_timeout", SignalFamily.FILE_INTEGRITY),
    }
    assert all(s.values[0] is None for s in result.series)
    assert all(s.coverage < 1.0 for s in result.series)


async def test_too_many_agents_are_truncated_and_silent_agents_disclosed() -> None:
    class Many(SyntheticWazuhApi):
        async def agents(self, names: Any, start: int, end: int) -> AgentList:
            found = await super().agents(names, start, end)
            return AgentList(found.agents, truncated=True)

    many = Many("healthy", INDEX, ["web-1"])
    result, _ = await collect(WazuhMetricsSource(many, INDEX, ["web-1"]))
    assert [e.code for e in result.exclusions] == ["series_truncated"]

    class Quiet(SyntheticWazuhApi):
        async def agents(self, names: Any, start: int, end: int) -> AgentList:
            found = await super().agents(names, start, end)
            return AgentList([a for a in found.agents if a.name != "gone"])

    quiet = Quiet("healthy", INDEX, ["gone", "web-1"])
    result, _ = await collect(WazuhMetricsSource(quiet, INDEX, ["gone", "web-1"]))
    assert [(e.code, "gone" in e.message) for e in result.exclusions] == [("no_data", True)]
    assert {s.entity.display_name for s in result.series} == {"web-1"}


async def test_group_members_join_named_agents() -> None:
    api = RecordingApi("healthy", INDEX, ["shop-db-1"], groups=["shop-web"])
    source = WazuhMetricsSource(api, INDEX, ["shop-db-1"], groups=["shop-web"])
    result, _ = await collect(source)
    assert {s.entity.display_name for s in result.series} == {
        "shop-db-1",
        "shop-web-1",
        "shop-web-2",
    }
    assert result.exclusions == []


async def test_a_group_without_agents_is_disclosed_and_selects_nothing() -> None:
    api = RecordingApi("healthy", INDEX, groups=["nope"])
    result, caps = await collect(WazuhMetricsSource(api, INDEX, groups=["nope"]))
    assert result.series == [], "an empty group must not fall back to every agent"
    assert {c.status for c in caps} == {CapabilityStatus.UNSUPPORTED}
    assert [(e.code, "nope" in e.message) for e in result.exclusions] == [("no_data", True)]
    assert api.calls == []


async def test_a_truncated_group_makes_the_report_partial() -> None:
    class Big(SyntheticWazuhApi):
        async def group_members(self, groups: Any, start: int, end: int) -> GroupMembers:
            found = await super().group_members(groups, start, end)
            return GroupMembers(found.members, truncated=True)

    big = Big("healthy", INDEX, groups=["shop-web"])
    result, _ = await collect(WazuhMetricsSource(big, INDEX, groups=["shop-web"]))
    assert [e.code for e in result.exclusions] == ["series_truncated"]


async def test_no_alerts_at_all_is_unsupported_not_healthy() -> None:
    class Empty(SyntheticWazuhApi):
        async def agents(self, names: Any, start: int, end: int) -> AgentList:
            return AgentList([])

    result, caps = await collect(WazuhMetricsSource(Empty("healthy", INDEX, ["x"]), INDEX, ["x"]))
    assert {c.status for c in caps} == {CapabilityStatus.UNSUPPORTED}
    assert caps[0].reason and "No alerts" in caps[0].reason
    assert result.series == []


# --- synthetic scenarios through the pipeline -------------------------------------------------


async def run(*sources: OpenedSource) -> AnalysisReport:
    config = DetectorConfig()
    pipeline = AnalysisPipeline(
        StaticSources(*sources), RobustDetector(), config, FakeExplanationProvider()
    )
    request = AnalysisRequest(
        scope=make_scope(),
        end_time=END,
        detector_version=config.version,
        config_hash=config.config_hash,
    )
    return await pipeline.run(
        "01999a2b-0000-7000-8000-0000000000f1", request, Progress(), CancellationToken()
    )


def synthetic(scenario: str) -> OpenedSource:
    agents = ["db-1", "web-1"]
    api = SyntheticWazuhApi(scenario, INDEX, agents)
    return OpenedSource(SourceKind.WAZUH, WazuhMetricsSource(api, INDEX, agents))


async def test_incident_yields_brute_force_and_fim_findings() -> None:
    report = await run(synthetic("incident"))
    by_agent: dict[str, set[str]] = {}
    for f in report.findings:
        by_agent.setdefault(f.entity.display_name, set()).add(f.signal)
    assert {"hids_auth_failure_rate", "hids_high_alert_rate"} <= by_agent["db-1"]
    assert "fim_change_rate" in by_agent["web-1"]
    assert "fim_change_rate" not in by_agent["db-1"]
    assert [s.kind for s in report.sources] == [SourceKind.WAZUH]
    assert {f.severity.value for f in report.findings} <= {"low", "medium", "high"}


async def test_healthy_and_short_history_yield_no_findings() -> None:
    for scenario in ("healthy", "short-history"):
        report = await run(synthetic(scenario))
        assert report.findings == [], scenario
        assert report.state is ReportState.COMPLETED


async def test_degraded_is_partial() -> None:
    report = await run(synthetic("degraded"))
    assert report.state is ReportState.PARTIAL
    assert {e.code for e in report.exclusions} == {"query_failed"}


async def test_wazuh_auth_failure_makes_a_mixed_report_partial() -> None:
    conn = WazuhConnection(
        api_url="https://indexer:9200", agents=["web-1"], username="u", password=SecretStr(PASSWORD)
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    async with connect_wazuh(conn, transport=transport) as wazuh:
        report = await run(
            OpenedSource(SourceKind.PROMETHEUS, SyntheticMetricsSource("incident")),
            OpenedSource(SourceKind.WAZUH, wazuh),
        )
    assert report.state is ReportState.PARTIAL
    assert report.findings
    failed = [e for e in report.exclusions if e.code == "source_unavailable"]
    assert {e.family for e in failed} == {SignalFamily.HOST_SECURITY, SignalFamily.FILE_INTEGRITY}
    assert PASSWORD not in report.model_dump_json()


async def test_probe_reports_alerts_and_families() -> None:
    conn = WazuhConnection(api_url="synthetic://healthy", agents=["web-1"])
    async with connect_wazuh(conn) as source:
        test = await probe_wazuh(source, SCOPE)
    assert test.kind is SourceKind.WAZUH
    assert test.reachable and test.auth_ok
    assert test.matched_series and test.matched_series > 0
    assert test.history_days == float(HISTORY_DAYS)
    assert {f.family for f in test.families} == {
        SignalFamily.HOST_SECURITY,
        SignalFamily.FILE_INTEGRITY,
    }

    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    async with connect_wazuh(
        WazuhConnection(
            api_url="https://indexer:9200", agents=["a"], username="u", password=SecretStr(PASSWORD)
        ),
        transport=transport,
    ) as source:
        denied = await probe_wazuh(source, SCOPE)
    assert denied.reachable and denied.auth_ok is False
    assert PASSWORD not in (denied.message or "")


async def test_probe_lists_group_members() -> None:
    conn = WazuhConnection(api_url="synthetic://healthy", groups=["shop-web", "nope"])
    async with connect_wazuh(conn) as source:
        test = await probe_wazuh(source, SCOPE)
    assert test.message is not None
    assert test.message.endswith("Groups: nope none, shop-web 2 agents.")


# --- project API ------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path) -> Iterator[TestClient]:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    with TestClient(create_app(settings)) as client:
        yield client


def wazuh_body(**source: Any) -> dict[str, Any]:
    return {
        "name": "Shop hosts",
        "sources": [
            {"kind": "wazuh", "api_url": "https://indexer:9200", "agents": ["web-1"], **source}
        ],
    }


def test_wazuh_password_is_write_only(api: TestClient, tmp_path: Path) -> None:
    missing = api.post("/api/projects", json=wazuh_body(username="reader"))
    assert missing.status_code == 422
    assert [e["field"] for e in missing.json()["errors"]] == ["body.sources.0.password"]

    labels = [{"key": "project", "value": "shop"}]
    res = api.post(
        "/api/projects",
        json=wazuh_body(username="reader", password=PASSWORD, labels=labels, tls_verify=False),
    )
    assert res.status_code == 201, res.text
    project = res.json()
    assert project["sources"] == [
        {
            "kind": "wazuh",
            "api_url": "https://indexer:9200",
            "index_pattern": INDEX,
            "agents": ["web-1"],
            "groups": [],
            "labels": labels,
            "monitoring_index_pattern": "wazuh-monitoring-*",
            "username": "reader",
            "tls_verify": False,
            "password_set": True,
        }
    ]
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        raw = db.execute("SELECT config, secrets FROM project_sources").fetchone()
    assert PASSWORD not in raw[0] and PASSWORD.encode() not in raw[1]

    pid = project["project_id"]
    body = wazuh_body(username="reader", agents=["web-1", "web-2"])
    assert api.put(f"/api/projects/{pid}", json=body).json()["sources"][0]["password_set"]
    clone = api.post(f"/api/projects?clone_of={pid}", json={**body, "name": "copy"})
    assert clone.status_code == 201 and clone.json()["sources"][0]["password_set"]
    assert PASSWORD not in api.get("/api/projects").text

    selectless = api.post("/api/projects", json=wazuh_body(agents=[], name="x"))
    assert selectless.status_code == 422
    grouped = api.post(
        "/api/projects", json={**wazuh_body(agents=[], groups=["web"]), "name": "by group"}
    )
    assert grouped.status_code == 201, grouped.text
    assert grouped.json()["sources"][0]["groups"] == ["web"]


def test_synthetic_wazuh_project_end_to_end(api: TestClient) -> None:
    bad = api.post("/api/projects", json=wazuh_body(api_url="synthetic://nope"))
    assert [e["field"] for e in bad.json()["errors"]] == ["body.sources.0.api_url"]

    project = api.post("/api/projects", json=wazuh_body(api_url="synthetic://incident")).json()
    health = api.get(f"/api/projects/{project['project_id']}/health").json()
    assert [h["kind"] for h in health] == ["wazuh"]
    assert health[0]["reachable"] and health[0]["auth_ok"]

    submitted = api.post("/api/analyses", json={"project_id": project["project_id"]})
    analysis_id = submitted.json()["analysis"]["analysis_id"]
    deadline = time.monotonic() + 60
    while (job := api.get(f"/api/analyses/{analysis_id}").json())["state"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert job["state"] == "completed", job
    report = api.get(f"/api/analyses/{analysis_id}/report").json()
    assert [s["kind"] for s in report["sources"]] == ["wazuh"]
    assert any(f["family"] == "host_security" for f in report["findings"])
