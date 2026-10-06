"""Sentry source (T017): REST client, collection, detection, and project API."""

import json
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
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
from app.domain.projects import SentryConnection, SentrySourceInput, SentryTag
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows, ReportState
from app.main import create_app
from app.service import AnalysisPipeline
from app.settings import load_settings
from app.sources.base import ClientLimits
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from app.sources.sentry.api import Chunk, ProjectInfo
from app.sources.sentry.catalog import Query
from app.sources.sentry.client import SentryClient
from app.sources.sentry.connect import connect_sentry, probe_sentry
from app.sources.sentry.source import SentryMetricsSource
from app.sources.sentry.synthetic import SyntheticSentryApi
from tests.helpers import StaticSources, make_scope

ORG = "acme"
PROJECT = "shop-web"
TOKEN = "sntrys_s3cr3t-token-value"
END = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
SCOPE = Scope(project_id="p-sentry", project_name="Shop app", matchers=[])
Handler = Callable[[httpx.Request], httpx.Response]


PROJECT_JSON = {
    "id": "42",
    "slug": PROJECT,
    "name": "Shop web",
    "dateCreated": "2026-01-02T03:04:05Z",
}


def with_project(handler: Handler) -> Handler:
    """Answers the project details request (numeric ID lookup); everything else to ``handler``."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/projects/{ORG}/{PROJECT}/"):
            return httpx.Response(200, json=PROJECT_JSON)
        return handler(request)

    return route


def make_client(handler: Handler, api_url: str = "https://sentry.io") -> SentryClient:
    conn = SentryConnection(
        organization=ORG,
        projects=[PROJECT],
        environment="production",
        tags=[SentryTag(key="team", value='shop "eu"')],
        api_url=api_url,
        auth_token=SecretStr(TOKEN),
    )
    return SentryClient(
        conn,
        limits=ClientLimits(timeout_seconds=5, retries=1),
        transport=httpx.MockTransport(handler),
    )


def timeseries(
    axes: dict[str, list[tuple[datetime, float | None]]],
    key: str = "timeSeries",
    interval: int = 300,
) -> dict[str, Any]:
    """An events-timeseries body; Sentry 25.x uses ``timeseries`` and ms intervals."""
    return {
        key: [
            {
                "yAxis": axis,
                "values": [
                    {"timestamp": int(ts.timestamp() * 1000), "value": v, "incomplete": False}
                    for ts, v in points
                ],
                "groupBy": [],
                "meta": {"valueUnit": None, "valueType": "integer", "interval": interval},
            }
            for axis, points in axes.items()
        ],
        "meta": {"dataset": "errors"},
    }


class Progress:
    async def update(self, progress: StageProgress) -> None:
        pass


# --- source input ---------------------------------------------------------------------------


def test_source_input_normalises_and_validates() -> None:
    source = SentrySourceInput(
        organization=" Acme ",
        projects=["Shop-Web", "api", "shop-web"],
        environment=" ",
        tags=[{"key": "team", "value": "shop"}, {"key": "server_name", "value": "web 1"}],
    )
    assert (source.organization, source.projects, source.environment) == (
        "acme",
        ["api", "shop-web"],
        None,
    )
    assert [t.key for t in source.tags] == ["server_name", "team"]
    assert source.api_url == "https://sentry.io"

    with pytest.raises(ValidationError, match="slug"):
        SentrySourceInput(organization="acme corp", projects=[PROJECT])
    with pytest.raises(ValidationError, match="invalid project slugs"):
        SentrySourceInput(organization=ORG, projects=["shop web"])
    with pytest.raises(ValidationError):
        SentrySourceInput(organization=ORG, projects=[])
    with pytest.raises(ValidationError):
        SentrySourceInput(organization=ORG, projects=[f"p{i}" for i in range(11)])
    with pytest.raises(ValidationError, match="duplicate tag keys"):
        SentrySourceInput(
            organization=ORG,
            projects=[PROJECT],
            tags=[{"key": "team", "value": "a"}, {"key": "team", "value": "b"}],
        )
    with pytest.raises(ValidationError, match="max 32"):
        SentrySourceInput(organization=ORG, projects=[PROJECT], tags=[{"key": "a b", "value": "x"}])
    with pytest.raises(ValidationError, match="environment"):
        SentrySourceInput(organization=ORG, projects=[PROJECT], environment="prod/eu")
    with pytest.raises(ValidationError):
        SentrySourceInput(organization=ORG, projects=[PROJECT], api_url="https://u:p@sentry.io")


# --- REST client ------------------------------------------------------------------------------


async def test_errors_request_and_parsing() -> None:
    sent: list[httpx.Request] = []
    bucket = END - timedelta(minutes=5)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        sent.append(request)
        return httpx.Response(
            200, json=timeseries({"count()": [(bucket, 6)], "count_unique(user)": [(bucket, 2)]})
        )

    client = make_client(with_project(handler), "https://sentry.example.com/prefix")
    chunk = await client.series(
        Query.ERRORS, ["42", "43"], int((END - timedelta(days=1)).timestamp()), int(END.timestamp())
    )
    await client.aclose()

    url = sent[0].url
    assert url.path == f"/prefix/api/0/organizations/{ORG}/events-timeseries/"
    assert url.params.get_list("yAxis") == ["count()", "count_unique(user)"]
    assert url.params["dataset"] == "errors"
    assert url.params.get_list("project") == ["42", "43"], "numeric IDs: 25.x rejects slugs"
    assert url.params["query"] == 'team:"shop \\"eu\\""', "tags are quoted search terms"
    assert url.params["environment"] == "production"
    assert url.params["interval"] == "5m"
    assert url.params["end"] == "2026-10-06T10:00:00Z"
    ts = int(bucket.timestamp())
    assert chunk.values == {"sentry_errors": {ts: 6.0}, "sentry_error_users": {ts: 2.0}}
    assert chunk.query.startswith(f"GET /api/0/organizations/{ORG}/events-timeseries/?")


async def test_transactions_convert_failures_and_durations() -> None:
    busy, idle = END - timedelta(minutes=10), END - timedelta(minutes=5)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["dataset"] == "spans"
        assert request.url.params["query"].startswith("is_transaction:true team:")
        return httpx.Response(
            200,
            json=timeseries(
                {
                    "count()": [(busy, 400), (idle, 0)],
                    "failure_rate()": [(busy, 0.05), (idle, 0)],
                    "p95(span.duration)": [(busy, 250.0), (idle, 0)],
                    "p99(span.duration)": [(busy, 900.0), (idle, None)],
                }
            ),
        )

    client = make_client(with_project(handler))
    chunk = await client.series(Query.TRANSACTIONS, ["42"], 0, int(END.timestamp()))
    await client.aclose()
    b, i = int(busy.timestamp()), int(idle.timestamp())
    assert chunk.values["sentry_transactions"] == {b: 400.0, i: 0.0}
    assert chunk.values["sentry_transaction_failures"] == {b: 20.0, i: 0.0}
    assert chunk.values["sentry_duration_p95"] == {b: 0.25}, "no duration without traffic"
    assert chunk.values["sentry_duration_p99"] == {b: 0.9}


async def test_self_hosted_25_response_and_transactions_dataset() -> None:
    """Sentry 25.5.1: ``timeseries`` key, interval in ms, classic transactions dataset."""
    bucket = END - timedelta(minutes=5)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["dataset"] == "transactions"
        assert request.url.params["query"].startswith("team:"), "no is_transaction filter"
        assert "p95(transaction.duration)" in request.url.params.get_list("yAxis")
        body: dict[str, list[tuple[datetime, float | None]]] = {
            "count()": [(bucket, 100)],
            "failure_rate()": [(bucket, 0.1)],
            "p95(transaction.duration)": [(bucket, 400.0)],
            "p99(transaction.duration)": [(bucket, 1200.0)],
        }
        return httpx.Response(200, json=timeseries(body, key="timeseries", interval=300_000))

    client = make_client(with_project(handler))
    client.transactions_dataset = "transactions"
    chunk = await client.series(Query.TRANSACTIONS, ["42"], 0, int(END.timestamp()))
    await client.aclose()
    ts = int(bucket.timestamp())
    assert chunk.values["sentry_transactions"] == {ts: 100.0}
    assert chunk.values["sentry_transaction_failures"] == {ts: 10.0}
    assert chunk.values["sentry_duration_p95"] == {ts: 0.4}
    assert "dataset=transactions" in chunk.query


async def test_wrong_bucket_size_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = timeseries({"count()": [(END, 1)]})
        body["timeSeries"][0]["meta"]["interval"] = 3600
        return httpx.Response(200, json=body)

    client = make_client(with_project(handler))
    with pytest.raises(SourceError, match="instead of 5 minutes"):
        await client.series(Query.UNHANDLED, ["42"], 0, int(END.timestamp()))
    await client.aclose()


@pytest.mark.parametrize(
    ("response", "kind", "text"),
    [
        (httpx.Response(401, json={"detail": "Invalid token"}), SourceErrorKind.AUTH, "401"),
        (httpx.Response(403), SourceErrorKind.AUTH, "project:read"),
        (httpx.Response(404), SourceErrorKind.AUTH, "not found"),
        (
            httpx.Response(302, headers={"Location": "https://de.sentry.io/"}),
            SourceErrorKind.BAD_QUERY,
            "de.sentry.io",
        ),
        (httpx.Response(502), SourceErrorKind.SERVER_ERROR, "502"),
    ],
)
async def test_errors_are_classified_without_the_token(
    response: httpx.Response, kind: SourceErrorKind, text: str
) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(SourceError) as caught:
        await client.projects()
    await client.aclose()
    assert caught.value.kind is kind
    assert text in caught.value.message
    assert TOKEN not in caught.value.message


async def test_self_hosted_url_keeps_its_prefix_and_explains_untrusted_certificates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("http://sentry.internal:9000/sentry/api/0/projects/")
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")

    client = make_client(handler, "http://sentry.internal:9000/sentry")
    with pytest.raises(SourceError, match="turn off TLS verification"):
        await client.projects()
    await client.aclose()


def test_tls_verification_is_stored_and_defaults_on(api: TestClient) -> None:
    on = api.post("/api/projects", json=sentry_body(api_url="synthetic://healthy")).json()
    assert on["sources"][0]["tls_verify"] is True
    body = sentry_body(api_url="https://sentry.internal/sentry", tls_verify=False, auth_token=TOKEN)
    off = api.post("/api/projects", json={**body, "name": "internal"}).json()
    assert off["sources"][0]["tls_verify"] is False
    assert off["sources"][0]["api_url"] == "https://sentry.internal/sentry"


async def test_rate_limit_is_retried_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=PROJECT_JSON)

    client = make_client(handler)
    [info] = await client.projects()
    await client.aclose()
    assert calls == 2
    assert (info.id, info.name) == ("42", "Shop web")
    assert info.created == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


# --- collection over a fake API ---------------------------------------------------------------


class FakeApi:
    """Every other bucket has 3 errors and 300 transactions; the chunk at ``fail_at`` fails.

    ``untraced`` adds a second project (ID 43) that sends errors but no transactions.
    ``spans`` says how the spans dataset answers: with data, ``empty`` or ``refused`` (as on
    self-hosted Sentry, which keeps transactions in the transactions dataset).
    """

    base_url = "https://sentry.io"
    backend = "sentry"

    def __init__(
        self,
        created: datetime | None = None,
        fail_at: int | None = None,
        transactions: float = 300.0,
        refuse: Query | None = None,
        spans: str = "data",
        untraced: bool = False,
    ) -> None:
        self.transactions_dataset = "spans"
        self.spans = spans
        self.created = created
        self.fail_at = fail_at
        self.transactions = transactions
        self.refuse = refuse
        self.untraced = untraced
        self.calls: list[tuple[Query, tuple[str, ...], int, int]] = []

    @property
    def slugs(self) -> list[str]:
        return [PROJECT, "worker"] if self.untraced else [PROJECT]

    def begin(self, end_time: datetime) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def projects(self) -> list[ProjectInfo]:
        ids = {PROJECT: "42", "worker": "43"}
        return [ProjectInfo(id=ids[s], slug=s, name=s, created=self.created) for s in self.slugs]

    async def series(self, query: Query, project_ids: Sequence[str], start: int, end: int) -> Chunk:
        self.calls.append((query, tuple(project_ids), start, end))
        if query is self.refuse:
            raise SourceError(SourceErrorKind.BAD_QUERY, "HTTP 400: dataset not available")
        if start == self.fail_at:
            raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 502")
        spans = query is Query.TRANSACTIONS and self.transactions_dataset == "spans"
        if spans and self.spans == "refused":
            raise SourceError(SourceErrorKind.BAD_QUERY, "HTTP 400: dataset must be one of ...")
        empty = (spans and self.spans == "empty") or list(project_ids) == ["43"]
        buckets = range(start, end, 600)
        values = {
            Query.ERRORS: {"sentry_errors": 3.0, "sentry_error_users": 1.0},
            Query.UNHANDLED: {"sentry_unhandled": 0.0},
            Query.TRANSACTIONS: {
                "sentry_transactions": 0.0 if empty else self.transactions,
                "sentry_transaction_failures": 0.0 if empty else 3.0,
                "sentry_duration_p95": 0.3,
                "sentry_duration_p99": 0.8,
            },
        }[query]
        return Chunk(
            query="GET fake", values={s: dict.fromkeys(buckets, v) for s, v in values.items()}
        )


async def collect(api: FakeApi) -> tuple[list[Any], Any]:
    source = SentryMetricsSource(
        api, ORG, api.slugs, "production", [SentryTag(key="team", value="shop")]
    )
    windows = AnalysisWindows.for_end(END)
    caps = await source.capabilities(SCOPE, windows)
    result = await source.collect(SCOPE, windows, caps, Progress(), CancellationToken())
    return caps, result


async def test_each_project_is_its_own_entity_and_untraced_ones_skip_transactions() -> None:
    api = FakeApi(untraced=True)
    _, result = await collect(api)

    probes = [ids for q, ids, s, e in api.calls if e - s == 86400 and q is Query.ERRORS]
    assert probes[0] == ("42", "43"), "capability probes cover all projects in one request"
    errors = {s.entity.labels["project"] for s in result.series if s.signal == "sentry_errors"}
    assert errors == {PROJECT, "worker"}
    traced = {
        s.entity.labels["project"] for s in result.series if s.signal == "sentry_transactions"
    }
    assert traced == {PROJECT}, "a project without transactions has no transaction series"
    worker = next(s for s in result.series if s.entity.labels["project"] == "worker")
    assert worker.entity.labels["tags"] == "team=shop"
    assert (
        worker.entity.key
        == "application|organization=acme|project=worker|environment=production|tags=team=shop"
    )


async def test_collection_chunks_start_at_project_creation() -> None:
    api = FakeApi(created=END - timedelta(days=10, minutes=2))
    caps, result = await collect(api)

    errors_calls = [(s, e) for q, _, s, e in api.calls if q is Query.ERRORS and e - s > 86400]
    assert all(e - s <= 7 * 86400 for s, e in errors_calls), "chunks are at most seven days"
    assert min(s for s, _ in errors_calls) >= int((END - timedelta(days=10)).timestamp()) - 300

    errors = next(s for s in result.series if s.signal == "sentry_errors")
    assert errors.entity.kind is EntityKind.APPLICATION
    assert errors.entity.labels == {
        "organization": ORG,
        "project": PROJECT,
        "environment": "production",
        "tags": "team=shop",
    }
    assert errors.entity.display_name == f"{PROJECT} (production)"
    assert errors.values[0] is None, "before the project existed: unknown, not zero"
    assert errors.values[-2:] in ([0.01, 0.0], [0.0, 0.01]), "missing buckets are zero"
    p95 = next(s for s in result.series if s.signal == "sentry_duration_p95")
    assert None in p95.values[-2:], "a missing duration stays unknown"
    assert "events-timeseries" in errors.query and "$start" in errors.query
    assert {c.status for c in caps} == {CapabilityStatus.SUPPORTED}
    assert caps[0].history_days == 10.0


async def test_failed_chunks_are_disclosed() -> None:
    fail_at = int((END - timedelta(days=14)).timestamp())  # the third of four 7-day chunks
    _, result = await collect(FakeApi(fail_at=fail_at))
    codes = {(e.code, e.family) for e in result.exclusions}
    assert ("query_failed", SignalFamily.APP_ERRORS) in codes
    assert ("query_failed", SignalFamily.APP_PERFORMANCE) in codes
    errors = next(s for s in result.series if s.signal == "sentry_errors")
    failed = errors.values[-14 * 288 : -7 * 288]
    assert set(failed) == {None}, "a failed chunk is unknown, never zero"
    assert None not in errors.values[-7 * 288 :]


async def test_without_transactions_performance_is_unsupported() -> None:
    caps, result = await collect(FakeApi(transactions=0.0))
    perf = [c for c in caps if c.family is SignalFamily.APP_PERFORMANCE]
    assert {c.status for c in perf} == {CapabilityStatus.UNSUPPORTED}
    assert all("tracing" in (c.reason or "") for c in perf)
    assert {s.family for s in result.series} == {SignalFamily.APP_ERRORS}


@pytest.mark.parametrize("spans", ["empty", "refused"])
async def test_transactions_fall_back_to_the_transactions_dataset(spans: str) -> None:
    api = FakeApi(spans=spans)
    caps, result = await collect(api)
    perf = [c for c in caps if c.family is SignalFamily.APP_PERFORMANCE]
    assert {c.status for c in perf} == {CapabilityStatus.SUPPORTED}
    assert api.transactions_dataset == "transactions"
    duration = next(c for c in caps if c.signal == "sentry_duration_p95")
    assert duration.observed_metrics == ["events-timeseries:transactions:p95(transaction.duration)"]
    assert any(s.signal == "sentry_transactions" for s in result.series)


async def test_refused_dataset_is_unsupported_with_reason() -> None:
    caps, _ = await collect(FakeApi(refuse=Query.UNHANDLED))
    unhandled = next(c for c in caps if c.signal == "sentry_unhandled")
    assert unhandled.status is CapabilityStatus.UNSUPPORTED
    assert "dataset not available" in (unhandled.reason or "")


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
        "01999a2b-0000-7000-8000-0000000000e1", request, Progress(), CancellationToken()
    )


def synthetic(scenario: str) -> OpenedSource:
    projects = [PROJECT, "worker"]
    api = SyntheticSentryApi(scenario, ORG, projects, "production")
    return OpenedSource(SourceKind.SENTRY, SentryMetricsSource(api, ORG, projects, "production"))


async def test_incident_yields_error_and_performance_findings() -> None:
    report = await run(synthetic("incident"))
    signals = {f.signal for f in report.findings}
    assert {"app_error_rate", "app_unhandled_error_rate", "app_error_users"} <= signals
    assert {"app_transaction_failure_ratio", "app_duration_p95"} <= signals
    assert {c.family for c in report.coverage} == {
        SignalFamily.APP_ERRORS,
        SignalFamily.APP_PERFORMANCE,
    }
    assert [s.kind for s in report.sources] == [SourceKind.SENTRY]
    projects = {f.entity.labels["project"] for f in report.findings}
    assert projects == {PROJECT}, "only the first project had the incident"


async def test_healthy_yields_no_findings() -> None:
    report = await run(synthetic("healthy"))
    assert report.findings == []
    assert report.state is ReportState.COMPLETED


async def test_degraded_is_partial_with_slow_transactions() -> None:
    report = await run(synthetic("degraded"))
    assert report.state is ReportState.PARTIAL
    signals = {f.signal for f in report.findings}
    assert {"app_duration_p95", "app_transaction_rate"} <= signals


async def test_sentry_auth_failure_makes_a_mixed_report_partial() -> None:
    conn = SentryConnection(organization=ORG, projects=[PROJECT], auth_token=SecretStr(TOKEN))
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    async with connect_sentry(conn, transport=transport) as sentry:
        report = await run(
            OpenedSource(SourceKind.PROMETHEUS, SyntheticMetricsSource("incident")),
            OpenedSource(SourceKind.SENTRY, sentry),
        )
    assert report.state is ReportState.PARTIAL
    assert report.findings
    failed = [e for e in report.exclusions if e.code == "source_unavailable"]
    assert {e.family for e in failed} == {SignalFamily.APP_ERRORS, SignalFamily.APP_PERFORMANCE}
    assert TOKEN not in report.model_dump_json()


async def test_probe_reports_events_and_families() -> None:
    conn = SentryConnection(organization=ORG, projects=[PROJECT], api_url="synthetic://healthy")
    async with connect_sentry(conn) as source:
        test = await probe_sentry(source, SCOPE)
    assert test.kind is SourceKind.SENTRY
    assert test.reachable and test.auth_ok
    assert test.matched_series and test.matched_series > 0
    assert {f.family for f in test.families} == {
        SignalFamily.APP_ERRORS,
        SignalFamily.APP_PERFORMANCE,
    }

    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    async with connect_sentry(
        SentryConnection(organization=ORG, projects=[PROJECT], auth_token=SecretStr(TOKEN)),
        transport=transport,
    ) as source:
        denied = await probe_sentry(source, SCOPE)
    assert denied.reachable and denied.auth_ok is False
    assert TOKEN not in (denied.message or "")


# --- project API ------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path) -> Iterator[TestClient]:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    with TestClient(create_app(settings)) as client:
        yield client


def sentry_body(**source: Any) -> dict[str, Any]:
    return {
        "name": "Shop app",
        "sources": [{"kind": "sentry", "organization": ORG, "projects": [PROJECT], **source}],
    }


def test_sentry_project_token_is_write_only(api: TestClient, tmp_path: Path) -> None:
    missing = api.post("/api/projects", json=sentry_body())
    assert missing.status_code == 422
    assert [e["field"] for e in missing.json()["errors"]] == ["body.sources.0.auth_token"]

    tags = [{"key": "team", "value": "shop"}]
    res = api.post(
        "/api/projects",
        json=sentry_body(
            auth_token=TOKEN, environment="production", projects=["web", "api"], tags=tags
        ),
    )
    assert res.status_code == 201, res.text
    project = res.json()
    assert project["sources"] == [
        {
            "kind": "sentry",
            "organization": ORG,
            "projects": ["api", "web"],
            "environment": "production",
            "tags": tags,
            "api_url": "https://sentry.io",
            "tls_verify": True,
            "token_set": True,
        }
    ]
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        raw = db.execute("SELECT config, secrets FROM project_sources").fetchone()
    assert TOKEN not in raw[0] and TOKEN.encode() not in raw[1]

    pid = project["project_id"]
    body = sentry_body(environment="staging")
    assert api.put(f"/api/projects/{pid}", json=body).json()["sources"][0]["token_set"]
    clone = api.post(f"/api/projects?clone_of={pid}", json={**body, "name": "copy"})
    assert clone.status_code == 201 and clone.json()["sources"][0]["token_set"]
    assert TOKEN not in api.get("/api/projects").text


def test_single_project_sources_saved_earlier_still_load(api: TestClient, tmp_path: Path) -> None:
    pid = api.post("/api/projects", json=sentry_body(api_url="synthetic://healthy")).json()[
        "project_id"
    ]
    legacy = json.dumps(
        {
            "organization": ORG,
            "project": PROJECT,
            "environment": None,
            "api_url": "synthetic://healthy",
        }
    )
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        db.execute("UPDATE project_sources SET config = ?", (legacy,))
    source = api.get(f"/api/projects/{pid}").json()["sources"][0]
    assert (source["projects"], source["tags"]) == ([PROJECT], [])
    assert api.get(f"/api/projects/{pid}/health").json()[0]["reachable"]


def test_synthetic_sentry_project_end_to_end(api: TestClient) -> None:
    bad = api.post("/api/projects", json=sentry_body(api_url="synthetic://nope"))
    assert [e["field"] for e in bad.json()["errors"]] == ["body.sources.0.api_url"]

    project = api.post("/api/projects", json=sentry_body(api_url="synthetic://incident")).json()
    health = api.get(f"/api/projects/{project['project_id']}/health").json()
    assert [h["kind"] for h in health] == ["sentry"]
    assert health[0]["reachable"] and health[0]["auth_ok"]

    submitted = api.post("/api/analyses", json={"project_id": project["project_id"]})
    analysis_id = submitted.json()["analysis"]["analysis_id"]
    deadline = time.monotonic() + 60
    while (job := api.get(f"/api/analyses/{analysis_id}").json())["state"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert job["state"] == "completed", job
    report = api.get(f"/api/analyses/{analysis_id}/report").json()
    assert [s["kind"] for s in report["sources"]] == ["sentry"]
    assert any(f["family"] == "app_errors" for f in report["findings"])
