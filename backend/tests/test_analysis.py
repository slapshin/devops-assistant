from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.analysis.baseline import STEPS_PER_DAY, bucket_baseline
from app.analysis.derive import _TRAFFIC_SPECS, DIRECT, SWARM_FAILED_TASKS, AnalysisSeries, derive
from app.analysis.detect import TREND_STEPS, evaluate
from app.analysis.engine import RobustDetector, severity_from_points, trend_summary
from app.analysis.rules import RULES
from app.domain.common import STEP_SECONDS, Entity, EntityKind, Severity, SignalFamily, Unit
from app.domain.detector_config import DetectorConfig
from app.domain.explanation import ExplanationResult, ExplanationStatus
from app.domain.findings import (
    BaselineMode,
    DetectionMethod,
    Finding,
    FindingState,
    Recurrence,
    SignalStatus,
    TrendBucketStatus,
    TrendDirection,
)
from app.domain.interfaces import CancellationToken, CollectionResult, DetectionResult
from app.domain.jobs import StageProgress
from app.domain.metrics import MetricCapability, MetricSeries
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows, ReportState
from app.sources.prometheus.catalog import CATALOG
from app.sources.prometheus.synthetic import N, Scenario, SyntheticMetricsSource, _SeriesBuilder
from tests.helpers import make_scope

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


async def collect(scenario: Scenario | str) -> tuple[list[MetricCapability], CollectionResult]:
    source = SyntheticMetricsSource(scenario)
    caps = await source.capabilities(SCOPE, WINDOWS)
    return caps, await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())


async def detect(scenario: Scenario | str) -> DetectionResult:
    caps, col = await collect(scenario)
    return RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)


def replace_values(
    col: CollectionResult, signal: str, entity_part: str, values: np.ndarray
) -> CollectionResult:
    series: list[MetricSeries] = []
    for s in col.series:
        if s.signal == signal and s.entity.key.endswith(entity_part):
            vals = [None if v is None or np.isnan(v) else float(v) for v in values]
            s = s.model_copy(
                update={
                    "values": vals,
                    "coverage": sum(v is not None for v in vals) / len(vals),
                }
            )
        series.append(s)
    return col.model_copy(update={"series": series})


def by_signal(result: DetectionResult, signal: str) -> list[Finding]:
    return [f for f in result.findings if f.signal == signal]


# --- scenarios ---------------------------------------------------------------------------


async def test_healthy_data_has_no_findings_and_full_coverage() -> None:
    result = await detect("healthy")
    assert result.findings == []
    assert all(t.status is TrendBucketStatus.OK and t.episode_count == 0 for t in result.trends)
    statuses = {c.family: c.status for c in result.coverage}
    assert statuses[SignalFamily.CPU] is SignalStatus.NO_ANOMALY
    assert statuses[SignalFamily.DISK_IO] is SignalStatus.UNSUPPORTED  # never "healthy"
    assert result.trend_summary is not None
    assert result.trend_summary.direction is TrendDirection.STABLE


async def test_sustained_cpu_load() -> None:
    result = await detect(Scenario(cpu_load=True))
    (cpu,) = by_signal(result, "cpu_utilization")
    assert cpu.entity.labels == {"job": "node", "instance": "paas-production"}
    assert cpu.start == T - timedelta(hours=5) and cpu.end == T - timedelta(hours=2)
    assert cpu.method is DetectionMethod.ABSOLUTE and cpu.threshold == 0.90
    assert cpu.severity is Severity.CRITICAL and cpu.confidence.value == "high"
    assert cpu.expected is not None and cpu.expected.median < 0.35
    assert cpu.baseline_mode is BaselineMode.TIME_OF_DAY and cpu.baseline_days == 14
    assert cpu.state is FindingState.RESOLVED and cpu.recurrence is Recurrence.NEW
    ev = next(e for e in result.evidence if e.evidence_id == cpu.evidence_ids[0])
    assert ev.series.query.startswith("1 - avg by (job, instance)")
    assert ev.threshold == 0.90 and "not an SLO" in (ev.threshold_label or "")
    assert len(ev.series.values) == len(ev.expected or []) == 72 + 36 + 24  # clipped at T


async def test_memory_growth_is_ongoing() -> None:
    (mem,) = by_signal(await detect(Scenario(memory_growth=True)), "memory_utilization")
    assert mem.state is FindingState.ONGOING and mem.end == T
    assert mem.severity is Severity.CRITICAL


async def test_disk_depletion_reaches_critical_heuristic() -> None:
    (fs,) = by_signal(await detect(Scenario(disk_depletion=True)), "filesystem_used_ratio")
    assert fs.threshold == 0.95 and fs.observed.value >= 0.95
    assert fs.entity.labels["mountpoint"] == "/"


async def test_independent_404_and_5xx_bursts_stay_separate() -> None:
    result = await detect(Scenario(not_found_burst=True, server_error_burst=True))
    (nf,) = by_signal(result, "not_found_rate")
    (err,) = by_signal(result, "server_error_ratio")
    assert nf.family is SignalFamily.CLIENT_ERRORS and nf.title == "404 increase"
    assert nf.severity in (Severity.LOW, Severity.MEDIUM)  # 4xx never above medium
    assert err.family is SignalFamily.REQUEST_FAILURES and err.threshold == 0.05
    assert nf.finding_id not in err.related_finding_ids  # different times
    assert by_signal(result, "client_error_rate") == []  # 404s are not double counted


async def test_latency_shift_and_mean_labelling() -> None:
    (p95,) = by_signal(await detect(Scenario(latency_shift=True)), "latency_p95")
    assert p95.unit if hasattr(p95, "unit") else True
    assert p95.observed.unit is Unit.SECONDS and p95.observed.value > 0.35
    mean_only = await detect(Scenario(histogram=False))
    latency = next(c for c in mean_only.coverage if c.family is SignalFamily.LATENCY)
    assert latency.status is SignalStatus.NO_ANOMALY
    assert RULES["latency_mean"].label == "Mean latency"


async def test_traffic_drop() -> None:
    (drop,) = by_signal(await detect(Scenario(traffic_drop=True)), "request_rate")
    assert drop.title == "Traffic drop" and drop.peak_score is not None and drop.peak_score < 0


async def test_low_volume_route_ratio_is_not_evaluated() -> None:
    caps, col = await collect("healthy")
    errors = np.full(N, np.nan)
    errors[-30:] = 0.05  # 100 % errors, but only ~15 requests per step
    series = col.series + [
        s.model_copy(
            update={
                "signal": "http_5xx",
                "values": [None if np.isnan(v) else v for v in errors],
                "coverage": 30 / N,
            }
        )
        for s in col.series
        if s.signal == "http_requests" and "task-sla" in s.entity.key
    ]
    result = RobustDetector().detect(
        REQUEST, WINDOWS, caps, col.model_copy(update={"series": series}), CONFIG
    )
    assert by_signal(result, "server_error_ratio") == []


async def test_gap_is_not_healthy_and_lowers_confidence() -> None:
    caps, col = await collect(Scenario(cpu_load=True))
    cpu = next(
        s
        for s in col.series
        if s.signal == "cpu_utilization" and s.entity.labels["instance"] == "paas-production"
    )
    values = np.array([np.nan if v is None else v for v in cpu.values])
    values[N - 70 : N - 50] = np.nan  # gap overlapping the start of the load
    result = RobustDetector().detect(
        REQUEST,
        WINDOWS,
        caps,
        replace_values(col, "cpu_utilization", "instance=paas-production", values),
        CONFIG,
    )
    (finding,) = by_signal(result, "cpu_utilization")
    assert any(r.code.startswith("coverage") for r in finding.confidence_reasons)
    assert finding.confidence.value != "high"


async def test_zero_variance_baseline_uses_scale_floor() -> None:
    caps, col = await collect(Scenario(flat_memory=True))
    flat = np.full(N, 0.35)
    small = flat.copy()
    small[-40:] = 0.37  # +2 pp: below the +10 pp minimum effect
    none = RobustDetector().detect(
        REQUEST, WINDOWS, caps, replace_values(col, "memory_utilization", "-2", small), CONFIG
    )
    assert by_signal(none, "memory_utilization") == []
    big = flat.copy()
    big[-40:] = 0.60
    hit = RobustDetector().detect(
        REQUEST, WINDOWS, caps, replace_values(col, "memory_utilization", "-2", big), CONFIG
    )
    (f,) = by_signal(hit, "memory_utilization")
    assert f.peak_score is not None and np.isfinite(f.peak_score)
    assert f.method is DetectionMethod.RELATIVE and f.expected is not None


async def test_short_retention() -> None:
    result = await detect("short-history")
    (cpu,) = by_signal(result, "cpu_utilization")
    assert cpu.baseline_days == 4 and cpu.confidence.value == "medium"
    statuses = [t.status for t in result.trends]
    assert statuses[:2] == [TrendBucketStatus.OK, TrendBucketStatus.OK]
    assert all(s is not TrendBucketStatus.OK for s in statuses[2:])
    assert result.trend_summary is not None
    assert result.trend_summary.direction is TrendDirection.INCONCLUSIVE


async def test_no_baseline_means_absolute_only_with_low_confidence() -> None:
    result = await detect(Scenario(history_days=2, cpu_load=True))
    (cpu,) = by_signal(result, "cpu_utilization")
    assert cpu.method is DetectionMethod.ABSOLUTE and cpu.expected is None
    assert cpu.confidence.value == "low"
    cov = next(c for c in result.coverage if c.family is SignalFamily.MEMORY)
    assert cov.status is SignalStatus.INSUFFICIENT_DATA


# --- invariants ----------------------------------------------------------------------------


def test_baseline_uses_only_preceding_days() -> None:
    rng = np.random.default_rng(1)
    values = rng.normal(0.5, 0.01, 28 * STEPS_PER_DAY)
    lo = 20 * STEPS_PER_DAY
    before = bucket_baseline(values, lo, CONFIG)
    tampered = values.copy()
    tampered[lo:] = 99.0  # the evaluated day and everything after
    after = bucket_baseline(tampered, lo, CONFIG)
    assert np.array_equal(before.median, after.median)
    assert np.array_equal(before.scale, after.scale)
    assert before.days == 14


async def test_future_data_does_not_change_earlier_buckets() -> None:
    _caps, col = await collect("healthy")
    base = [evaluate(s, CONFIG) for s in derive(col.series, 300, 30)]
    changed = col.model_copy(
        update={
            "series": [
                s.model_copy(
                    update={
                        "values": s.values[:-STEPS_PER_DAY] + [5.0] * STEPS_PER_DAY,
                        "coverage": 1.0,
                    }
                )
                if s.signal == "cpu_utilization"
                else s
                for s in col.series
            ]
        }
    )
    after = [evaluate(s, CONFIG) for s in derive(changed.series, 300, 30)]
    for a, b in zip(base, after, strict=True):
        if a.series.rule.name != "cpu_utilization":
            continue
        for bucket in range(1, 14):
            assert np.array_equal(a.baselines[bucket].median, b.baselines[bucket].median)


async def test_detection_is_deterministic() -> None:
    first = await detect("incident")
    second = await detect("incident")
    assert first.model_dump() == second.model_dump()
    assert [f.finding_id for f in first.findings] == [f.finding_id for f in second.findings]


async def test_findings_are_ordered_by_severity() -> None:
    result = await detect("incident")
    order = ["critical", "high", "medium", "low"]
    ranks = [order.index(f.severity.value) for f in result.findings]
    assert ranks == sorted(ranks)


async def test_recurrence_across_days() -> None:
    result = await detect(Scenario(not_found_burst=True, recurring_404_days=(3, 5, 9)))
    (nf,) = by_signal(result, "not_found_rate")
    assert nf.recurrence is Recurrence.RECURRING and nf.prior_episode_days == 3
    days = [
        t.bucket_index
        for t in result.trends
        if any(e.signal == "not_found_rate" for e in t.episodes)
    ]
    assert days == [0, 3, 5, 9]
    latest = next(e for e in result.trends[0].episodes if e.signal == "not_found_rate")
    assert latest.finding_id == nf.finding_id
    older = next(e for e in result.trends[3].episodes if e.signal == "not_found_rate")
    assert older.finding_id is None


async def test_changing_population_does_not_masquerade_as_deterioration() -> None:
    result = await detect(Scenario(new_host_days=3))
    assert result.findings == []
    minutes = [t.observed_entity_minutes for t in result.trends]
    assert minutes[0] > minutes[5]  # a host joined
    assert all(t.anomalous_share == 0 for t in result.trends if t.anomalous_share is not None)


async def test_cross_layer_relations_need_verified_mapping() -> None:
    result = await detect(Scenario(cpu_load=True, server_error_burst=True))
    (cpu,) = by_signal(result, "cpu_utilization")  # paas-production
    (err,) = by_signal(result, "server_error_ratio")  # dispatcher-api -> paas-production-2
    assert cpu.end > err.start  # they overlap in time...
    assert err.finding_id not in cpu.related_finding_ids  # ...but share no verified host
    assert err.attributes["host"] == "paas-production-2"
    degraded = await detect("degraded")
    (fs,) = by_signal(degraded, "filesystem_used_ratio")  # on paas-production-2
    (lat,) = by_signal(degraded, "latency_p95")
    assert lat.finding_id in fs.related_finding_ids  # verified service -> host mapping


async def test_proxy_upstream_outage_relates_to_proxy_server_errors() -> None:
    result = await detect(Scenario(proxy_outage=True))
    (down,) = by_signal(result, "upstream_unavailable")
    (err,) = by_signal(result, "server_error_ratio")
    assert down.family is SignalFamily.PROXY and down.entity.kind is EntityKind.UPSTREAM
    assert err.entity.kind is EntityKind.PROXY
    assert err.entity.labels == {"job": "traefik", "service": "dispatcher-api@swarm"}
    assert err.finding_id in down.related_finding_ids  # same proxy job, overlapping
    assert "host" not in err.attributes  # no verified proxy -> host mapping
    coverage = {c.family: c.status for c in result.coverage}
    assert coverage[SignalFamily.PROXY] is SignalStatus.ANOMALOUS
    healthy = await detect("healthy")
    assert {c.family: c.status for c in healthy.coverage}[SignalFamily.PROXY] is (
        SignalStatus.NO_ANOMALY
    )


async def test_database_pileup_findings_relate_within_the_server() -> None:
    result = await detect(Scenario(database_pileup=True))
    database = {f.signal: f for f in result.findings if f.family is SignalFamily.DATABASE}
    assert set(database) == {
        "database_longest_transaction",
        "database_connections_ratio",
        "database_rollback_ratio",
        "database_deadlocks",
    }
    connections = database["database_connections_ratio"]
    assert connections.entity.labels == {"job": "postgres", "instance": "pg-primary:9187"}
    assert database["database_deadlocks"].entity.labels["datname"] == "dispatcher"
    others = {f.finding_id for s, f in database.items() if s != "database_connections_ratio"}
    assert others <= set(connections.related_finding_ids)  # server and its database
    assert "host" not in connections.attributes  # no verified exporter -> host mapping
    healthy = await detect("healthy")
    assert {c.family: c.status for c in healthy.coverage}[SignalFamily.DATABASE] is (
        SignalStatus.NO_ANOMALY
    )


async def test_database_relations_need_the_same_server() -> None:
    caps, col = await collect("healthy")
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    down = np.zeros(N)
    down[N - 40 : N - 30] = 1.0
    for instance in ("db1:9187", "db2:9187"):
        builder.add("pg_down", {"job": "postgres", "instance": instance}, down)
    col = col.model_copy(update={"series": [*col.series, *builder.series]})
    result = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    findings = by_signal(result, "database_down")
    assert len(findings) == 2
    assert not any(f.related_finding_ids for f in findings)  # same job, different servers


async def test_mysql_contention_findings_relate_within_the_primary() -> None:
    result = await detect(Scenario(mysql_contention=True))
    mysql = {f.signal: f for f in result.findings if f.entity.labels.get("job") == "mysql"}
    assert set(mysql) == {
        "database_connections_ratio",
        "database_connections_refused",
        "database_slow_query_ratio",
        "database_lock_waits",
    }
    assert {f.entity.labels["instance"] for f in mysql.values()} == {"mysql-primary:9104"}
    connections = mysql["database_connections_ratio"]
    assert connections.entity.kind is EntityKind.DATABASE
    others = {f.finding_id for s, f in mysql.items() if s != "database_connections_ratio"}
    assert others == set(connections.related_finding_ids)  # not the PostgreSQL server


def test_mysql_signals_map_to_database_rules() -> None:
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    replica = {"job": "mysql", "instance": "db2:9104"}
    builder.add("mysql_queries", replica, np.full(N, 100.0))
    builder.add("mysql_slow_queries", replica, np.full(N, 0.1))
    builder.add("mysql_replica_lag", replica, np.zeros(N))
    builder.add("mysql_replica_stopped", replica, np.zeros(N))
    builder.add("mysql_connections_refused", replica, np.zeros(N))
    rules = sorted(s.rule.name for s in derive(builder.series, STEP_SECONDS, 30))
    assert rules == [
        "database_connections_refused",
        "database_query_rate",
        "database_replication_lag",
        "database_replication_stopped",
        "database_slow_query_ratio",
    ]


async def test_mysql_replication_stop_is_a_shortfall() -> None:
    caps, col = await collect("healthy")
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    stopped = np.zeros(N)
    stopped[N - 40 : N - 30] = 1.0
    builder.add("mysql_replica_stopped", {"job": "mysql", "instance": "db2:9104"}, stopped)
    col = col.model_copy(update={"series": [*col.series, *builder.series]})
    result = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    [finding] = by_signal(result, "database_replication_stopped")
    assert finding.title == "Replication stopped (SQL or I/O thread not running)"


async def test_redis_eviction_storm_findings_relate_within_the_master() -> None:
    result = await detect(Scenario(redis_eviction_storm=True))
    redis = {f.signal: f for f in result.findings if f.entity.labels.get("job") == "redis"}
    assert set(redis) == {
        "database_memory_ratio",
        "database_evictions",
        "database_cache_miss_ratio",
        "database_command_latency",
    }
    assert {f.entity.display_name for f in redis.values()} == {"redis · redis://redis-master:6379"}
    memory = redis["database_memory_ratio"]
    others = {f.finding_id for s, f in redis.items() if s != "database_memory_ratio"}
    assert others == set(memory.related_finding_ids)  # not the replica or other databases
    assert redis["database_command_latency"].severity is not Severity.LOW


def test_redis_signals_map_to_database_rules() -> None:
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    replica = {"job": "redis", "instance": "cache2:6379"}
    for signal in (
        "redis_commands",
        "redis_command_latency_mean",
        "redis_keyspace_lookups",
        "redis_keyspace_misses",
        "redis_replica_link_down",
        "redis_persistence_failed",
        "redis_rejected_connections",
    ):
        builder.add(signal, replica, np.full(N, 1.0))
    rules = sorted(s.rule.name for s in derive(builder.series, STEP_SECONDS, 30))
    assert rules == [  # lookups only guard the miss share
        "database_cache_miss_ratio",
        "database_command_latency",
        "database_command_rate",
        "database_connections_refused",
        "database_persistence_failed",
        "database_replica_link_down",
    ]


async def test_redis_replica_link_down_is_a_shortfall() -> None:
    caps, col = await collect("healthy")
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    down = np.zeros(N)
    down[N - 40 : N - 30] = 1.0
    builder.add("redis_replica_link_down", {"job": "redis", "instance": "cache2:6379"}, down)
    col = col.model_copy(update={"series": [*col.series, *builder.series]})
    result = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    [finding] = by_signal(result, "database_replica_link_down")
    assert finding.title == "Replica disconnected from its master"


def test_every_catalog_signal_is_consumed_by_derivation() -> None:
    consumed: set[str | None] = {*DIRECT, SWARM_FAILED_TASKS}
    for spec in _TRAFFIC_SPECS:
        consumed |= {spec.traffic_signal, spec.quantile_signal, spec.mean_signal, spec.tail_signal}
        consumed |= {numerator for numerator, _ in spec.error_ratios}
        consumed |= set(spec.client_errors or ())
    assert {d.signal for d in CATALOG} <= consumed
    assert set(DIRECT.values()) <= set(RULES)


def test_nginx_signals_map_to_generic_proxy_rules() -> None:
    builder = _SeriesBuilder(SCOPE, WINDOWS)
    nginx = {"job": "nginx", "instance": "10.0.0.5:9113"}
    builder.add("nginx_requests", nginx, np.full(N, 50.0))
    builder.add("nginx_connections_dropped", nginx, np.zeros(N))
    builder.add("nginx_down", nginx, np.zeros(N))
    rules = sorted(s.rule.name for s in derive(builder.series, STEP_SECONDS, 30))
    assert rules == ["proxy_connections_dropped", "proxy_down", "request_rate"]  # no status codes


def test_episode_merging_and_boundary_split() -> None:
    config = CONFIG
    values = np.full(28 * STEPS_PER_DAY, 0.2)
    rng = np.random.default_rng(3)
    values += rng.normal(0, 0.005, values.size)
    # 4 anomalous steps, 2-step gap, 4 anomalous steps across the latest-day boundary
    boundary = 28 * STEPS_PER_DAY - STEPS_PER_DAY
    for i in list(range(boundary - 6, boundary - 2)) + list(range(boundary, boundary + 4)):
        values[i] = 0.95
    series = AnalysisSeries(
        RULES["cpu_utilization"], _node_entity(), Unit.RATIO, values, "q", _dummy_source()
    )
    ev = evaluate(series, config)
    assert len(ev.episodes) == 1
    ep = ev.episodes[0]
    assert (ep.start, ep.end) == (TREND_STEPS - STEPS_PER_DAY - 6, TREND_STEPS - STEPS_PER_DAY + 4)
    assert ep.buckets() == {0, 1}


def test_severity_mapping_and_cap() -> None:
    assert [severity_from_points(p, None) for p in (0, 1, 2, 3, 4, 5, 6)] == [
        Severity.LOW,
        Severity.LOW,
        Severity.MEDIUM,
        Severity.HIGH,
        Severity.HIGH,
        Severity.CRITICAL,
        Severity.CRITICAL,
    ]
    assert severity_from_points(6, Severity.MEDIUM) is Severity.MEDIUM


def test_trend_summary_with_sparse_data_is_inconclusive() -> None:
    assert trend_summary([]).direction is TrendDirection.INCONCLUSIVE


async def test_detection_result_composes_a_valid_report() -> None:
    caps, col = await collect("incident")
    result = RobustDetector().detect(REQUEST, WINDOWS, caps, col, CONFIG)
    report = AnalysisReport(
        analysis_id="01999a2b-0000-7000-8000-000000000001",
        scope=SCOPE,
        windows=WINDOWS,
        generated_at=T,
        detector_version=CONFIG.version,
        config_hash=CONFIG.config_hash,
        source=await SyntheticMetricsSource().source_info(),
        state=ReportState.COMPLETED,
        capabilities=caps,
        coverage=result.coverage,
        findings=result.findings,
        trends=result.trends,
        trend_summary=result.trend_summary,
        evidence=result.evidence,
        exclusions=result.exclusions,
        explanation=ExplanationResult(status=ExplanationStatus.DISABLED),
    )
    assert len(report.model_dump_json()) < 20 * 1024 * 1024


def _node_entity() -> Entity:
    return Entity(
        kind=EntityKind.NODE,
        key="node|job=node|instance=h",
        display_name="node · h",
        labels={"job": "node", "instance": "h"},
    )


def _dummy_source() -> MetricSeries:
    return MetricSeries(
        series_id="ser_0000000000000000",
        family=SignalFamily.CPU,
        signal="cpu_utilization",
        entity=_node_entity(),
        labels={},
        unit=Unit.RATIO,
        query="q",
        step_seconds=300,
        start=T - timedelta(days=28),
        values=[],
        coverage=0.0,
    )


@pytest.mark.parametrize("name", sorted(RULES))
def test_every_rule_has_thresholds_or_is_event(name: str) -> None:
    rule = RULES[name]
    assert rule.kind.value != "level" or rule.thresholds in CONFIG.signals
