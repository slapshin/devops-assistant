"""Cloudflare source (T015): GraphQL client, collection, detection, and project API."""

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
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
from app.domain.interfaces import (
    CancellationToken,
    OpenedSource,
    SourceError,
    SourceErrorKind,
)
from app.domain.jobs import StageProgress
from app.domain.metrics import CapabilityStatus
from app.domain.projects import CloudflareConnection, CloudflareSourceInput
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows, ReportState
from app.main import create_app
from app.service import AnalysisPipeline
from app.settings import load_settings
from app.sources.base import ClientLimits
from app.sources.cloudflare.api import (
    FIREWALL_DATASET,
    HTTP_DATASET,
    Chunk,
    DatasetSettings,
)
from app.sources.cloudflare.client import CloudflareClient
from app.sources.cloudflare.connect import connect_cloudflare, probe_cloudflare
from app.sources.cloudflare.source import CloudflareMetricsSource
from app.sources.cloudflare.synthetic import SyntheticCloudflareApi
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from tests.helpers import StaticSources, make_scope

ZONE = "0123456789abcdef0123456789abcdef"
TOKEN = "cf-s3cr3t-token-value"
END = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
SCOPE = Scope(project_id="p-cf", project_name="Shop edge", matchers=[])
Handler = Callable[[httpx.Request], httpx.Response]


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def zone_response(**aliases: Any) -> dict[str, Any]:
    return {"data": {"viewer": {"zones": [aliases]}}, "errors": None}


def make_client(handler: Handler, hostnames: list[str] | None = None) -> CloudflareClient:
    conn = CloudflareConnection(zone_id=ZONE, hostnames=hostnames or [], api_token=SecretStr(TOKEN))
    return CloudflareClient(
        conn,
        limits=ClientLimits(timeout_seconds=5, retries=1),
        transport=httpx.MockTransport(handler),
    )


class Progress:
    def __init__(self) -> None:
        self.updates: list[StageProgress] = []

    async def update(self, progress: StageProgress) -> None:
        self.updates.append(progress)


# --- source input ---------------------------------------------------------------------------


def test_source_input_normalises_and_validates() -> None:
    source = CloudflareSourceInput(
        zone_id=f" {ZONE.upper()} ",
        hostnames=["Shop.Example.com.", "api.example.com", "api.example.com"],
    )
    assert source.zone_id == ZONE
    assert source.hostnames == ["api.example.com", "shop.example.com"]
    assert source.api_url == "https://api.cloudflare.com/client/v4/graphql"

    with pytest.raises(ValidationError, match="zone ID"):
        CloudflareSourceInput(zone_id="not-a-zone")
    with pytest.raises(ValidationError, match="invalid hostnames"):
        CloudflareSourceInput(zone_id=ZONE, hostnames=['bad"host'])
    with pytest.raises(ValidationError):
        CloudflareSourceInput(zone_id=ZONE, api_url="https://user:pw@example.com/graphql")


# --- GraphQL client ---------------------------------------------------------------------------


async def test_traffic_query_and_parsing() -> None:
    sent: list[dict[str, Any]] = []
    bucket = END - timedelta(minutes=5)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        sent.append(json.loads(request.content))
        row = {"count": 600, "dimensions": {"datetimeFiveMinutes": iso(bucket)}}
        return httpx.Response(
            200,
            json=zone_response(
                requests=[{**row, "avg": {"sampleInterval": 4}}],
                status5xx=[{**row, "count": 6}],
                origin52x=[],
                status404=[{**row, "count": 30}],
                status4xx=[{**row, "count": 45}],
                cacheHits=[{**row, "count": 300}],
            ),
        )

    client = make_client(handler, ["shop.example.com"])
    chunk = await client.traffic(
        int((END - timedelta(days=1)).timestamp()), int(END.timestamp()), 500
    )
    await client.aclose()

    document = sent[0]["query"]
    assert f'zoneTag: "{ZONE}"' in document
    assert 'clientRequestHTTPHost_in: ["shop.example.com"]' in document
    assert "edgeResponseStatus_geq: 520, edgeResponseStatus_lt: 531" in document
    assert f'datetime_lt: "{iso(END)}"' in document
    ts = int(bucket.timestamp())
    assert chunk.values["cf_requests"] == {ts: 600.0}
    assert chunk.values["cf_5xx"] == {ts: 6.0}
    assert chunk.values["cf_52x"] == {}
    assert chunk.sample_intervals == {ts: 4.0}
    assert not chunk.truncated
    assert chunk.query == document


async def test_timing_in_seconds_and_security_actions() -> None:
    bucket = iso(END - timedelta(minutes=5))

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if FIREWALL_DATASET in query:
            rows = [
                {"count": 10, "dimensions": {"datetimeFiveMinutes": bucket, "action": a}}
                for a in ("block", "managed_challenge", "jschallenge", "log", "connectionClose")
            ]
            return httpx.Response(200, json=zone_response(events=rows))
        timing = {
            "quantiles": {
                "edgeTimeToFirstByteMsP95": 250,
                "edgeTimeToFirstByteMsP99": 900,
                "originResponseDurationMsP95": None,
            },
            "dimensions": {"datetimeFiveMinutes": bucket},
        }
        return httpx.Response(200, json=zone_response(timing=[timing]))

    client = make_client(handler)
    start, end = int((END - timedelta(hours=1)).timestamp()), int(END.timestamp())
    timing = await client.timing(start, end, 500)
    security = await client.security(start, end, 500)
    await client.aclose()

    ts = int((END - timedelta(minutes=5)).timestamp())
    assert timing.values["cf_ttfb_p95"] == {ts: 0.25}
    assert timing.values["cf_ttfb_p99"] == {ts: 0.9}
    assert timing.values["cf_origin_p95"] == {}
    assert security.values == {"cf_blocked": {ts: 20.0}, "cf_challenged": {ts: 20.0}}


@pytest.mark.parametrize(
    ("response", "kind", "text"),
    [
        (httpx.Response(401), SourceErrorKind.AUTH, "rejected the API token"),
        (
            httpx.Response(
                200, json={"data": None, "errors": [{"message": "authentication error"}]}
            ),
            SourceErrorKind.AUTH,
            "authentication error",
        ),
        (
            httpx.Response(200, json={"data": None, "errors": [{"message": "unknown field foo"}]}),
            SourceErrorKind.BAD_QUERY,
            "unknown field foo",
        ),
        (
            httpx.Response(200, json={"data": {"viewer": {"zones": []}}, "errors": None}),
            SourceErrorKind.AUTH,
            "not found",
        ),
        (httpx.Response(200, content=b"<html>"), SourceErrorKind.BAD_QUERY, "unexpected"),
    ],
)
async def test_errors_are_classified_without_the_token(
    response: httpx.Response, kind: SourceErrorKind, text: str
) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(SourceError) as caught:
        await client.settings()
    await client.aclose()
    assert caught.value.kind is kind
    assert text in caught.value.message
    assert TOKEN not in caught.value.message


async def test_rate_limit_is_retried_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json=zone_response(
                settings={
                    HTTP_DATASET: {
                        "enabled": True,
                        "maxDuration": 259200,
                        "notOlderThan": 2678400,
                        "maxPageSize": 10000,
                    }
                }
            ),
        )

    client = make_client(handler)
    settings = await client.settings()
    await client.aclose()
    assert calls == 2
    assert settings[HTTP_DATASET].max_duration == 259200
    assert not settings[FIREWALL_DATASET].enabled  # missing from the settings node


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"success": True, "result": {"id": ZONE, "name": "example.com"}}, "example.com"),
        (403, {"success": False, "errors": [{"message": "Authentication error"}]}, None),
        (200, {"success": True, "result": {}}, None),
    ],
)
async def test_zone_name_is_best_effort(
    status: int, body: dict[str, Any], expected: str | None
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    client = make_client(handler)
    assert await client.zone_name() == expected
    await client.aclose()
    assert seen[0].method == "GET"
    assert str(seen[0].url) == f"https://api.cloudflare.com/client/v4/zones/{ZONE}"


async def test_zone_name_skipped_without_a_rest_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    conn = CloudflareConnection(zone_id=ZONE, api_url="https://proxy.example.com/cf-analytics")
    client = CloudflareClient(conn, transport=httpx.MockTransport(handler))
    assert await client.zone_name() is None
    await client.aclose()


# --- collection over a fake API ---------------------------------------------------------------


class FakeApi:
    """Every bucket has 300 requests (1/s); chosen chunks fail or are truncated."""

    def __init__(
        self,
        settings: DatasetSettings | None = None,
        fail_from: int | None = None,
        truncate: bool = False,
        timing_error: SourceError | None = None,
        sample_interval: float = 1.0,
    ) -> None:
        self._settings = settings or DatasetSettings()
        self.fail_from = fail_from
        self.truncate = truncate
        self.timing_error = timing_error
        self.sample_interval = sample_interval
        self.calls: list[tuple[str, int, int]] = []
        self.name: str | None = "example.com"

    base_url = "https://api.cloudflare.com/client/v4/graphql"
    backend = "cloudflare"

    def begin(self, end_time: datetime) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def settings(self) -> dict[str, DatasetSettings]:
        return {HTTP_DATASET: self._settings, FIREWALL_DATASET: self._settings}

    async def zone_name(self) -> str | None:
        return self.name

    def _chunk(self, signals: dict[str, float], start: int, end: int) -> Chunk:
        chunk = Chunk(query="{ fake }", truncated=self.truncate)
        for signal, value in signals.items():
            # every other bucket only: missing rows inside a fetched chunk mean zero
            chunk.values[signal] = {b: value for b in range(start, end, 600)}
        chunk.sample_intervals = {b: self.sample_interval for b in range(start, end, 300)}
        return chunk

    async def traffic(self, start: int, end: int, page_size: int) -> Chunk:
        self.calls.append(("traffic", start, end))
        if self.fail_from is not None and start >= self.fail_from:
            raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 502")
        return self._chunk({"cf_requests": 300.0, "cf_5xx": 3.0}, start, end)

    async def timing(self, start: int, end: int, page_size: int) -> Chunk:
        self.calls.append(("timing", start, end))
        if self.timing_error is not None:
            raise self.timing_error
        return self._chunk({"cf_ttfb_p95": 0.2}, start, end)

    async def security(self, start: int, end: int, page_size: int) -> Chunk:
        self.calls.append(("security", start, end))
        return self._chunk({"cf_blocked": 30.0}, start, end)

    async def daily_requests(self, first: date, last: date) -> dict[date, float]:
        return {last - timedelta(days=d): 86400.0 for d in range(10)}


async def collect(api: FakeApi) -> tuple[list[Any], Any]:
    source = CloudflareMetricsSource(api, ZONE, ["shop.example.com"])
    windows = AnalysisWindows.for_end(END)
    caps = await source.capabilities(SCOPE, windows)
    result = await source.collect(SCOPE, windows, caps, Progress(), CancellationToken())
    return caps, result


async def test_collection_chunks_respect_retention_and_duration() -> None:
    api = FakeApi(DatasetSettings(max_duration=2 * 86400, not_older_than=10 * 86400))
    caps, result = await collect(api)

    traffic = [(s, e) for kind, s, e in api.calls if kind == "traffic" and e - s > 3600]
    assert all(e - s <= 86400 for s, e in traffic), "chunks are at most one day"
    end = int(END.timestamp())
    assert min(s for s, _ in traffic) > end - 10 * 86400, "never older than retention"

    requests = next(s for s in result.series if s.signal == "cf_requests")
    assert requests.entity.kind is EntityKind.ZONE
    assert requests.entity.labels == {"zone_id": ZONE, "hostnames": "shop.example.com"}
    assert requests.values[0] is None, "before retention: unknown, not zero"
    assert requests.values[-2:] in ([1.0, 0.0], [0.0, 1.0]), "missing rows are zero"
    assert requests.sample_interval == 1.0
    ttfb = next(s for s in result.series if s.signal == "cf_ttfb_p95")
    assert None in ttfb.values[-2:], "a missing quantile row stays unknown"
    assert "$start" in requests.query and "GraphQL" in requests.query
    assert {c.status for c in caps} == {CapabilityStatus.SUPPORTED}
    assert caps[0].history_days is not None


async def test_zone_domain_names_the_entity_without_changing_its_key() -> None:
    api = FakeApi()
    source = CloudflareMetricsSource(api, ZONE, [])
    key = source.entity.key
    assert source.entity.display_name == f"zone {ZONE[:8]}…"
    await source.capabilities(SCOPE, AnalysisWindows.for_end(END))
    assert source.entity.display_name == "example.com"
    assert source.entity.key == key
    assert source.entity.labels == {"zone_id": ZONE}

    api.name = None
    unnamed = CloudflareMetricsSource(api, ZONE, [])
    await unnamed.capabilities(SCOPE, AnalysisWindows.for_end(END))
    assert unnamed.entity.display_name == f"zone {ZONE[:8]}…"

    hosts = CloudflareMetricsSource(FakeApi(), ZONE, ["shop.example.com"])
    await hosts.capabilities(SCOPE, AnalysisWindows.for_end(END))
    assert hosts.entity.display_name == "shop.example.com", "hostnames stay more specific"


async def test_failed_and_truncated_chunks_are_disclosed() -> None:
    fail_from = int((END - timedelta(days=2)).timestamp())
    _, result = await collect(FakeApi(fail_from=fail_from, truncate=True))

    codes = {(e.code, e.family) for e in result.exclusions}
    assert ("query_failed", SignalFamily.EDGE) in codes
    assert ("series_truncated", SignalFamily.SECURITY) in codes
    requests = next(s for s in result.series if s.signal == "cf_requests")
    assert requests.values[-1] is None, "a failed chunk is unknown, never zero"


async def test_refused_timing_is_unsupported_with_reason() -> None:
    error = SourceError(SourceErrorKind.BAD_QUERY, "zone plan does not allow quantiles")
    caps, result = await collect(FakeApi(timing_error=error))
    timing = [c for c in caps if c.signal.startswith(("cf_ttfb", "cf_origin"))]
    assert {c.status for c in timing} == {CapabilityStatus.UNSUPPORTED}
    assert all("Pro plan" in (c.reason or "") for c in timing)
    assert not any(s.signal == "cf_ttfb_p95" for s in result.series)


async def test_heavily_sampled_data_lowers_confidence() -> None:
    api = SyntheticCloudflareApi("incident", ZONE, [])
    source = CloudflareMetricsSource(api, ZONE, [])
    original = api.traffic

    async def sampled(start: int, end: int, page_size: int) -> Chunk:
        chunk = await original(start, end, page_size)
        chunk.sample_intervals = dict.fromkeys(chunk.sample_intervals, 150.0)
        return chunk

    api.traffic = sampled  # type: ignore[method-assign]
    report = await run(OpenedSource(SourceKind.CLOUDFLARE, source))
    errors = [f for f in report.findings if f.signal == "edge_server_error_ratio"]
    assert errors and errors[0].confidence.value == "low"
    assert any(r.code == "sampled_low" for r in errors[0].confidence_reasons)


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
        "01999a2b-0000-7000-8000-0000000000cf", request, Progress(), CancellationToken()
    )


def synthetic(scenario: str) -> OpenedSource:
    api = SyntheticCloudflareApi(scenario, ZONE, ["shop.example.com"])
    return OpenedSource(
        SourceKind.CLOUDFLARE, CloudflareMetricsSource(api, ZONE, ["shop.example.com"])
    )


async def test_incident_yields_edge_and_security_findings() -> None:
    report = await run(synthetic("incident"))
    signals = {f.signal for f in report.findings}
    assert {"edge_server_error_ratio", "edge_origin_error_ratio", "edge_ttfb_p95"} <= signals
    assert {"security_blocked_rate", "security_challenge_rate"} <= signals
    assert {c.family for c in report.coverage} == {SignalFamily.EDGE, SignalFamily.SECURITY}
    assert [s.kind for s in report.sources] == [SourceKind.CLOUDFLARE]
    assert report.sources[0].backend == "synthetic"


async def test_healthy_yields_no_findings() -> None:
    report = await run(synthetic("healthy"))
    assert report.findings == []
    assert report.state is ReportState.COMPLETED


async def test_prometheus_and_cloudflare_in_one_report() -> None:
    prometheus = OpenedSource(SourceKind.PROMETHEUS, SyntheticMetricsSource("healthy"))
    report = await run(prometheus, synthetic("incident"))
    families = {c.family for c in report.coverage}
    assert {SignalFamily.CPU, SignalFamily.EDGE, SignalFamily.SECURITY} <= families
    assert {f.family for f in report.findings} >= {SignalFamily.EDGE}
    assert [s.kind for s in report.sources] == [SourceKind.PROMETHEUS, SourceKind.CLOUDFLARE]


async def test_cloudflare_auth_failure_makes_a_mixed_report_partial() -> None:
    conn = CloudflareConnection(zone_id=ZONE, api_token=SecretStr(TOKEN))
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    async with connect_cloudflare(conn, transport=transport) as cloudflare:
        report = await run(
            OpenedSource(SourceKind.PROMETHEUS, SyntheticMetricsSource("incident")),
            OpenedSource(SourceKind.CLOUDFLARE, cloudflare),
        )
    assert report.state is ReportState.PARTIAL
    assert report.findings
    failed = [e for e in report.exclusions if e.code == "source_unavailable"]
    assert {e.family for e in failed} == {SignalFamily.EDGE, SignalFamily.SECURITY}
    assert TOKEN not in report.model_dump_json()


async def test_probe_reports_requests_and_families() -> None:
    conn = CloudflareConnection(zone_id=ZONE, api_url="synthetic://healthy")
    async with connect_cloudflare(conn) as source:
        test = await probe_cloudflare(source, SCOPE)
    assert test.kind is SourceKind.CLOUDFLARE
    assert test.reachable and test.auth_ok
    assert test.matched_series and test.matched_series > 0
    assert test.message and test.message.startswith("healthy.example.com: ")
    assert {f.family for f in test.families} == {SignalFamily.EDGE, SignalFamily.SECURITY}

    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    async with connect_cloudflare(
        CloudflareConnection(zone_id=ZONE, api_token=SecretStr(TOKEN)), transport=transport
    ) as source:
        denied = await probe_cloudflare(source, SCOPE)
    assert denied.reachable and denied.auth_ok is False
    assert TOKEN not in (denied.message or "")


# --- project API ------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path) -> Iterator[TestClient]:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    with TestClient(create_app(settings)) as client:
        yield client


def cloudflare_body(**source: Any) -> dict[str, Any]:
    return {
        "name": "Shop edge",
        "sources": [
            {"kind": "cloudflare", "zone_id": ZONE, "hostnames": ["shop.example.com"], **source}
        ],
    }


def test_cloudflare_project_token_is_write_only(api: TestClient, tmp_path: Path) -> None:
    missing = api.post("/api/projects", json=cloudflare_body())
    assert missing.status_code == 422
    assert [e["field"] for e in missing.json()["errors"]] == ["body.sources.0.api_token"]

    res = api.post("/api/projects", json=cloudflare_body(api_token=TOKEN))
    assert res.status_code == 201, res.text
    project = res.json()
    assert project["matchers"] == []
    assert project["sources"] == [
        {
            "kind": "cloudflare",
            "zone_id": ZONE,
            "hostnames": ["shop.example.com"],
            "api_url": "https://api.cloudflare.com/client/v4/graphql",
            "token_set": True,
        }
    ]
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        raw = db.execute("SELECT config, secrets FROM project_sources").fetchone()
    assert TOKEN not in raw[0] and TOKEN.encode() not in raw[1]

    # an omitted token is kept on update and on clone
    pid = project["project_id"]
    body = cloudflare_body(hostnames=["api.example.com"])
    assert api.put(f"/api/projects/{pid}", json=body).json()["sources"][0]["token_set"]
    clone = api.post(f"/api/projects?clone_of={pid}", json={**body, "name": "copy"})
    assert clone.status_code == 201 and clone.json()["sources"][0]["token_set"]
    assert TOKEN not in api.get("/api/projects").text


def test_synthetic_cloudflare_project_end_to_end(api: TestClient) -> None:
    bad = api.post("/api/projects", json=cloudflare_body(api_url="synthetic://nope"))
    assert [e["field"] for e in bad.json()["errors"]] == ["body.sources.0.api_url"]

    body = {
        "name": "Shop",
        "matchers": [{"name": "project", "value": "shop"}],
        "sources": [
            {"kind": "prometheus", "url": "synthetic://healthy"},
            {"kind": "cloudflare", "zone_id": ZONE, "api_url": "synthetic://incident"},
        ],
    }
    project = api.post("/api/projects", json=body).json()
    health = api.get(f"/api/projects/{project['project_id']}/health").json()
    assert [h["kind"] for h in health] == ["cloudflare", "prometheus"]
    assert all(h["reachable"] for h in health)

    test = api.post(
        "/api/projects/test-connection",
        json={"source": {"kind": "cloudflare", "zone_id": ZONE, "api_url": "synthetic://healthy"}},
    ).json()
    assert test["kind"] == "cloudflare" and test["auth_ok"] is True

    submitted = api.post("/api/analyses", json={"project_id": project["project_id"]})
    analysis_id = submitted.json()["analysis"]["analysis_id"]
    deadline = time.monotonic() + 60
    while (job := api.get(f"/api/analyses/{analysis_id}").json())["state"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert job["state"] == "completed", job
    report = api.get(f"/api/analyses/{analysis_id}/report").json()
    assert [s["kind"] for s in report["sources"]] == ["cloudflare", "prometheus"]
    assert any(f["family"] == "edge" for f in report["findings"])
