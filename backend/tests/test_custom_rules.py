"""Custom rules: scope injection, rule model, collection, detection, API (T020)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.prompt import build_input
from app.analysis.engine import RobustDetector
from app.domain.common import EntityKind, Severity, SignalFamily, Unit
from app.domain.custom_rules import CustomRule, CustomRuleMode, ThresholdOp
from app.domain.detector_config import DetectorConfig
from app.domain.findings import DetectionMethod, Finding, SignalStatus
from app.domain.interfaces import CancellationToken, CollectionResult
from app.domain.jobs import StageProgress
from app.domain.metrics import CapabilityStatus
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows
from app.domain.user_promql import UserQueryError, scope_template
from app.main import create_app
from app.service import AnalysisPipeline
from app.settings import load_settings
from app.sources.prometheus.promql import QueryTemplate, assert_scoped
from app.sources.prometheus.source import PrometheusMetricsSource
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from tests.helpers import StaticSources, make_scope
from tests.test_metrics import FakeProm, matrix, present_handler, vector

T = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
WINDOWS = AnalysisWindows.for_end(T)
SCOPE = make_scope()
CONFIG = DetectorConfig()
REQUEST = AnalysisRequest(
    scope=SCOPE, end_time=T, detector_version=CONFIG.version, config_hash=CONFIG.config_hash
)


class Progress:
    async def update(self, progress: StageProgress) -> None:
        pass


def rule(**overrides: Any) -> CustomRule:
    fields: dict[str, Any] = {
        "slug": "checkout_failures",
        "title": "Checkout failures",
        "query": "sum by (job) (rate(orders_failed_total[5m]))",
        "unit": Unit.PER_SECOND,
        **overrides,
    }
    return CustomRule.model_validate(fields)


ANOMALY = rule()
THRESHOLD = rule(
    slug="queue_depth",
    title="Queue depth",
    query="max by (job) (queue_messages)",
    unit=Unit.COUNT,
    mode=CustomRuleMode.THRESHOLD,
    threshold=500,
    critical_threshold=900,
    min_duration_minutes=10,
)
BELOW = rule(
    slug="consumers",
    title="Queue consumers",
    query="sum by (job) (queue_consumers)",
    unit=Unit.COUNT,
    mode=CustomRuleMode.THRESHOLD,
    threshold=2,
    threshold_op=ThresholdOp.BELOW,
)
EVENT = rule(
    slug="failed_jobs",
    title="Failed batch jobs",
    query="sum by (job) (increase(batch_failures_total[5m]))",
    unit=Unit.COUNT,
    mode=CustomRuleMode.EVENT,
    max_severity=Severity.MEDIUM,
)
RULES = [ANOMALY, THRESHOLD, BELOW, EVENT]


# --- scope injection ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "rendered", "metrics"),
    [
        ("up", 'up{env="production", project="shop"}', ("up",)),
        (
            "sum by (job) (rate(x_total[$__window]))",
            'sum by (job) (rate(x_total{env="production", project="shop"}[5m]))',
            ("x_total",),
        ),
        (
            'rate(x{code=~"5..", path="{id}"}[5m])',
            'rate(x{env="production", project="shop", code=~"5..", path="{id}"}[5m])',
            ("x",),
        ),
        (
            "sum(rate(a[5m])) / (sum(rate(b[5m])) > 0)",
            'sum(rate(a{env="production", project="shop"}[5m])) / '
            '(sum(rate(b{env="production", project="shop"}[5m])) > 0)',
            ("a", "b"),
        ),
        (
            'histogram_quantile(0.95, sum without (instance) (rate(h_bucket{job="api"}[5m])))',
            "histogram_quantile(0.95, sum without (instance) "
            '(rate(h_bucket{env="production", project="shop", job="api"}[5m])))',
            ("h_bucket",),
        ),
        (
            'label_replace(up, "dst", "$1", "src", "(.*)")',
            'label_replace(up{env="production", project="shop"}, "dst", "$1", "src", "(.*)")',
            ("up",),
        ),
        (
            "a and on (job) group_left (team) b",
            'a{env="production", project="shop"} and on (job) group_left (team) '
            'b{env="production", project="shop"}',
            ("a", "b"),
        ),
        (
            '{__name__="up"}',
            '{env="production", project="shop", __name__="up"}',
            (),
        ),
        (
            "max_over_time(x[1h:5m]) offset 1d",
            'max_over_time(x{env="production", project="shop"}[1h:5m]) offset 1d',
            ("x",),
        ),
        ("x > bool 3", 'x{env="production", project="shop"} > bool 3', ("x",)),
        (
            "app:requests:rate5m # recording rule\n",
            'app:requests:rate5m{env="production", project="shop"}',
            ("app:requests:rate5m",),
        ),
    ],
)
def test_every_selector_gets_the_scope(query: str, rendered: str, metrics: tuple[str]) -> None:
    scoped = scope_template(query)

    out = QueryTemplate(scoped.template, scoped.metrics).render(SCOPE)

    assert out == rendered
    assert scoped.metrics == metrics
    assert_scoped(out, SCOPE, scoped.metrics)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("sum(x) > 5", "top-level comparison"),
        ("x == 0", "top-level comparison"),
        ("x @ 1700000000", "@ modifier"),
        ("x offset -1h", "negative offset"),
        ('x{a="b" or c="d"}', "'or' inside label matchers"),
        ("vector(1)", "selects no series"),
        ("sum(rate(x[5m])", "unbalanced"),
        ('x{a="b"', "unterminated"),
        ("rate(x[$__interval])", "unknown placeholder"),
        ("   ", "empty"),
    ],
)
def test_unsafe_or_malformed_queries_are_rejected(query: str, message: str) -> None:
    with pytest.raises(UserQueryError, match=message):
        scope_template(query)


# --- rule model --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"slug": "Bad-Slug"}, "must match"),
        ({"mode": "threshold"}, "threshold is required"),
        ({"threshold": 5}, "not used in anomaly mode"),
        ({"mode": "event", "threshold": 1}, "not used in event mode"),
        ({"mode": "both", "threshold": 5, "critical_threshold": 4}, "must be above"),
        (
            {"mode": "threshold", "threshold": 5, "critical_threshold": 6, "threshold_op": "below"},
            "must be below",
        ),
        ({"query": "sum(x) > 1"}, "top-level comparison"),
        ({"identity_labels": ["job", "job"]}, "duplicate"),
        ({"identity_labels": ["__name__"]}, "invalid label"),
        ({"min_change_factor": 1.0}, "greater than 1"),
    ],
)
def test_invalid_rules_are_rejected(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        rule(**overrides)


# --- Prometheus collection ---------------------------------------------------------------


async def test_custom_capabilities_need_metrics_and_a_working_query() -> None:
    present = {"orders_failed_total"}
    broken = rule(slug="broken", query="rate(orders_failed_total[5m]) / foo(1)")
    missing = rule(slug="missing", query="sum(rate(not_exported_total[5m]))")
    fallback = present_handler(present)

    def handler(path: str, params: dict[str, list[str]]) -> httpx.Response:
        if "foo(" in params.get("query", [""])[0]:
            return httpx.Response(400, json={"status": "error", "error": "unknown function foo"})
        return fallback(path, params)

    source = PrometheusMetricsSource(
        FakeProm(handler).client(), now=lambda: T, custom_rules=[ANOMALY, broken, missing]
    )

    caps = {c.signal: c for c in await source.capabilities(SCOPE, WINDOWS)}

    assert caps["custom_checkout_failures"].status is CapabilityStatus.SUPPORTED
    assert caps["custom_checkout_failures"].family is SignalFamily.CUSTOM
    assert caps["custom_broken"].status is CapabilityStatus.UNSUPPORTED
    assert "unknown function foo" in (caps["custom_broken"].reason or "")
    assert caps["custom_missing"].status is CapabilityStatus.UNSUPPORTED
    assert "not_exported_total" in (caps["custom_missing"].reason or "")


async def test_custom_series_are_collected_scoped_with_their_labels() -> None:
    start = int(T.timestamp()) - 86400
    series = matrix(
        (
            {"job": "checkout", "project": "shop", "env": "production"},
            [(start, "0.5"), (start + 300, "0.7")],
        ),
        ({"job": "billing", "project": "other"}, [(start, "9")]),
    )
    fake = FakeProm(present_handler({"orders_failed_total"}, {"orders_failed_total": series}))
    source = PrometheusMetricsSource(fake.client(), now=lambda: T, custom_rules=[ANOMALY])
    caps = await source.capabilities(SCOPE, WINDOWS)

    col = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    (collected,) = [s for s in col.series if s.signal == "custom_checkout_failures"]
    assert collected.family is SignalFamily.CUSTOM
    assert collected.entity.kind is EntityKind.CUSTOM
    assert collected.entity.labels == {"job": "checkout"}
    assert collected.entity.display_name == "checkout"
    assert 'orders_failed_total{env="production", project="shop"}' in collected.query
    assert col.custom_rules == [ANOMALY]
    assert any("foreign series" in e.message for e in col.exclusions)
    range_queries = [p["query"][0] for path, p in fake.requests if path.endswith("query_range")]
    assert all("orders_failed_total{env=" in q for q in range_queries if "orders" in q)


async def test_identity_labels_select_the_entity() -> None:
    start = int(T.timestamp()) - 86400
    series = matrix(({"job": "checkout", "instance": "h1"}, [(start, "1")]))
    by_job = rule(identity_labels=["job"])
    fake = FakeProm(present_handler({"orders_failed_total"}, {"orders_failed_total": series}))
    source = PrometheusMetricsSource(fake.client(), now=lambda: T, custom_rules=[by_job])
    caps = await source.capabilities(SCOPE, WINDOWS)

    col = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    (collected,) = [s for s in col.series if s.signal == "custom_checkout_failures"]
    assert collected.entity.labels == {"job": "checkout"}
    assert collected.entity.key == "custom|job=checkout"


async def test_preview_reports_series_and_query_errors() -> None:
    start = int(T.timestamp()) - 86400
    ok = matrix(({"job": "checkout"}, [(start + 300, "1"), (start + 600, "2")]))
    fake = FakeProm(lambda path, params: ok)
    source = PrometheusMetricsSource(fake.client(), now=lambda: T)

    preview = await source.preview_rule(ANOMALY, SCOPE, T, 300)

    assert preview.ok
    assert preview.series_count == 1
    assert preview.series[0].values[:2] == [1.0, 2.0]
    assert preview.series[0].latest == 2.0
    assert preview.query is not None
    assert 'project="shop"' in preview.query

    failing = FakeProm(lambda path, params: httpx.Response(400, json={"error": "parse error"}))
    source = PrometheusMetricsSource(failing.client(), now=lambda: T)

    preview = await source.preview_rule(ANOMALY, SCOPE, T, 300)

    assert not preview.ok
    assert preview.series == []
    assert "parse error" in (preview.message or "")


async def test_empty_preview_says_so() -> None:
    source = PrometheusMetricsSource(FakeProm(lambda path, params: vector()).client())

    preview = await source.preview_rule(ANOMALY, SCOPE, T, 300)

    assert preview.ok
    assert preview.series_count == 0
    assert "no series" in (preview.message or "")


# --- detection -----------------------------------------------------------------------------


async def run(scenario: str, rules: list[CustomRule]) -> AnalysisReport:
    pipeline = AnalysisPipeline(
        StaticSources(SyntheticMetricsSource(scenario, rules)), RobustDetector(), CONFIG, None
    )
    return await pipeline.run("a-1", REQUEST, Progress(), CancellationToken())


def custom_findings(report: AnalysisReport) -> dict[str, Finding]:
    found = [f for f in report.findings if f.family is SignalFamily.CUSTOM]
    return {f.signal: f for f in found}


async def test_incident_breaches_every_custom_rule_mode() -> None:
    report = await run("incident", RULES)

    found = custom_findings(report)

    assert set(found) == {r.signal for r in RULES}
    anomaly = found["custom_checkout_failures"]
    assert anomaly.method is DetectionMethod.RELATIVE
    assert anomaly.title == "Checkout failures above expected range"
    assert (anomaly.summary, anomaly.help) == (None, None)
    threshold = found["custom_queue_depth"]
    assert threshold.method is DetectionMethod.ABSOLUTE
    assert threshold.threshold == 900
    assert threshold.title == "Queue depth at or above 900 (threshold)"
    assert threshold.confidence_reasons == []
    below = found["custom_consumers"]
    assert below.threshold == 2
    assert below.title == "Queue consumers at or below 2 (threshold)"
    assert below.observed.value <= 2
    event = found["custom_failed_jobs"]
    assert event.severity is Severity.MEDIUM
    assert event.title == "Failed batch jobs"
    assert report.custom_rules == RULES
    evidence = {e.finding_id: e for e in report.evidence}
    assert evidence[threshold.finding_id].threshold_label == "Queue depth: custom rule threshold"
    (row,) = [c for c in report.coverage if c.family is SignalFamily.CUSTOM]
    assert row.status is SignalStatus.ANOMALOUS


async def test_healthy_custom_rules_have_no_findings() -> None:
    report = await run("healthy", RULES)

    assert custom_findings(report) == {}
    (row,) = [c for c in report.coverage if c.family is SignalFamily.CUSTOM]
    assert row.status is SignalStatus.NO_ANOMALY
    assert row.evaluated_series == len(RULES)


async def test_threshold_rules_need_no_baseline() -> None:
    report = await run("short-history", [THRESHOLD])

    (row,) = [c for c in report.coverage if c.family is SignalFamily.CUSTOM]
    assert row.status is SignalStatus.NO_ANOMALY


async def test_projects_without_custom_rules_have_no_custom_coverage() -> None:
    report = await run("incident", [])

    assert not [c for c in report.coverage if c.family is SignalFamily.CUSTOM]
    assert report.custom_rules == []


async def test_editing_a_rule_changes_its_finding_ids() -> None:
    source = SyntheticMetricsSource("incident", [ANOMALY])
    caps = await source.capabilities(SCOPE, WINDOWS)
    col = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())
    edited = col.model_copy(update={"custom_rules": [rule(min_change_factor=1.6)]})

    before = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    after = RobustDetector().detect(REQUEST, WINDOWS, caps, edited, CONFIG)

    assert {f.finding_id for f in before.findings}.isdisjoint(
        {f.finding_id for f in after.findings}
    )


async def test_series_without_their_rule_are_not_analysed() -> None:
    source = SyntheticMetricsSource("incident", [ANOMALY])
    caps = await source.capabilities(SCOPE, WINDOWS)
    col = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())
    orphaned = CollectionResult(series=col.series, exclusions=[], mappings=col.mappings)

    result = RobustDetector().detect(REQUEST, WINDOWS, caps, orphaned, CONFIG)

    assert not [f for f in result.findings if f.family is SignalFamily.CUSTOM]


async def test_rule_description_is_given_to_the_explanation() -> None:
    described = rule(description="Orders rejected by the payment provider.")
    report = await run("incident", [described])

    payload, _ = build_input(
        SCOPE,
        WINDOWS.latest_day,
        report.findings,
        report.coverage,
        custom_rules=report.custom_rules,
    )

    (digest,) = [d for d in payload.findings if d.family is SignalFamily.CUSTOM]
    assert digest.rule_description == "Orders rejected by the payment provider."
    others = [d for d in payload.findings if d.family is not SignalFamily.CUSTOM]
    assert all(d.rule_description is None for d in others)


# --- API -----------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Any:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    with TestClient(create_app(settings)) as c:
        yield c


def project_body(rules: list[dict[str, Any]], url: str = "synthetic://incident") -> dict[str, Any]:
    return {
        "name": "Shop",
        "matchers": [{"name": "project", "value": "shop"}],
        "sources": [{"kind": "prometheus", "url": url, "custom_rules": rules}],
    }


def rule_json(r: CustomRule) -> dict[str, Any]:
    return r.model_dump(mode="json")


def test_custom_rules_are_stored_with_the_source(client: TestClient) -> None:
    created = client.post("/api/projects", json=project_body([rule_json(ANOMALY)]))
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]

    fetched = client.get(f"/api/projects/{project_id}").json()

    (source,) = fetched["sources"]
    assert [r["slug"] for r in source["custom_rules"]] == ["checkout_failures"]

    body = project_body([rule_json(ANOMALY), rule_json(THRESHOLD)])
    updated = client.put(f"/api/projects/{project_id}", json=body)

    assert updated.status_code == 200, updated.text
    (source,) = updated.json()["sources"]
    assert [r["slug"] for r in source["custom_rules"]] == ["checkout_failures", "queue_depth"]


def test_invalid_custom_rules_are_rejected(client: TestClient) -> None:
    unsafe = {**rule_json(ANOMALY), "query": "x @ 100"}

    res = client.post("/api/projects", json=project_body([unsafe]))

    assert res.status_code == 422
    assert "@ modifier" in res.text

    duplicated = project_body([rule_json(ANOMALY), rule_json(ANOMALY)])

    res = client.post("/api/projects", json=duplicated)

    assert res.status_code == 422
    assert "duplicate rule slugs" in res.text


def test_preview_of_a_synthetic_rule(client: TestClient) -> None:
    body = {
        "source": {"kind": "prometheus", "url": "synthetic://healthy"},
        "matchers": [{"name": "project", "value": "shop"}],
        "rule": rule_json(THRESHOLD),
    }

    res = client.post("/api/projects/custom-rules/preview", json=body)

    assert res.status_code == 200, res.text
    preview = res.json()
    assert preview["ok"] is True
    assert preview["series_count"] == 1
    assert len(preview["series"][0]["values"]) == 288
    assert preview["series"][0]["entity"]["labels"] == {"job": "checkout-api"}
    assert 'queue_messages{project="shop"}' in preview["query"]


def test_preview_needs_a_restricting_matcher(client: TestClient) -> None:
    body = {
        "source": {"kind": "prometheus", "url": "synthetic://healthy"},
        "matchers": [],
        "rule": rule_json(ANOMALY),
    }

    res = client.post("/api/projects/custom-rules/preview", json=body)

    assert res.status_code == 422
