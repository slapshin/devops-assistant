"""Generate synthetic, sanitised shared fixtures from the contract models.

Usage: uv run python -m scripts.generate_fixtures [--check]

Labels are neutral and made up (project="shop", env="production",
node instance "shop-production", HTTP job "checkout-api"). Values are synthetic.
"""

import argparse
import asyncio
import json
import math
import random
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from app.ai.providers import FakeExplanationProvider
from app.analysis.engine import RobustDetector, trend_summary
from app.domain.common import (
    STEP_SECONDS,
    ConfidenceLevel,
    Entity,
    EntityKind,
    LabelMatcher,
    Reason,
    Scope,
    Severity,
    SignalFamily,
    SourceKind,
    TimeRange,
    Unit,
    format_utc,
)
from app.domain.detector_config import DetectorConfig
from app.domain.explanation import (
    Explanation,
    ExplanationResult,
    ExplanationStatus,
    Hypothesis,
    InvestigationStep,
    Likelihood,
)
from app.domain.findings import (
    BaselineMode,
    DailyTrend,
    DetectionMethod,
    EpisodeSummary,
    Evidence,
    ExpectedValue,
    Finding,
    ObservedValue,
    Recurrence,
    SignalCoverage,
    SignalStatus,
    TrendBucketStatus,
)
from app.domain.ids import episode_id, evidence_id, finding_id, series_id
from app.domain.interfaces import CancellationToken, OpenedSource
from app.domain.jobs import (
    AnalysisJob,
    AnalysisSubmitted,
    ErrorCode,
    JobError,
    JobState,
    Limits,
    Problem,
    RuntimeConfig,
    StageName,
    StageProgress,
    StageStatus,
)
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.projects import (
    BearerAuth,
    ConnectionTest,
    FamilyCapability,
    ProjectList,
    ProjectSummary,
    PrometheusSource,
    SentryTag,
    WazuhLabel,
)
from app.domain.report import (
    TREND_DAYS,
    AnalysisReport,
    AnalysisRequest,
    AnalysisWindows,
    Exclusion,
    ReportState,
    SourceInfo,
)
from app.domain.schedule import ReportSchedule, Weekday
from app.jobs import daily_episodes
from app.service import AnalysisPipeline
from app.sources.cloudflare.source import CloudflareMetricsSource
from app.sources.cloudflare.synthetic import SyntheticCloudflareApi
from app.sources.sentry.source import SentryMetricsSource
from app.sources.sentry.synthetic import SyntheticSentryApi
from app.sources.wazuh.source import WazuhMetricsSource
from app.sources.wazuh.synthetic import SyntheticWazuhApi

ROOT = Path(__file__).resolve().parents[2] / "fixtures"
PROJECT_ID = "01999a3c-0000-7000-8000-000000000001"
SCOPE = Scope(
    project_id=PROJECT_ID,
    project_name="shop / production",
    matchers=[
        LabelMatcher(name="env", value="production"),
        LabelMatcher(name="project", value="shop"),
    ],
)
SCOPE_LABELS = {m.name: m.value for m in SCOPE.matchers}
T = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
SCHEDULE = ReportSchedule(
    time="08:00", timezone="Europe/Berlin", weekdays=[Weekday.MON, Weekday.WED, Weekday.FRI]
)
STEP = timedelta(seconds=STEP_SECONDS)
CONFIG = DetectorConfig()
WINDOWS = AnalysisWindows.for_end(T)
SOURCE = SourceInfo(base_url="http://localhost:8428", backend=None, version=None)
SEL = 'project="shop", env="production"'

NODE = Entity(
    kind=EntityKind.NODE,
    key="node|job=node|instance=shop-production",
    display_name="node · shop-production",
    labels={"job": "node", "instance": "shop-production"},
)
ROUTE = Entity(
    kind=EntityKind.ROUTE,
    key="route|job=checkout-api|http_route=/api/v1/orders/:order|http_request_method=GET",
    display_name="checkout-api · GET /api/v1/orders/:order",
    labels={
        "job": "checkout-api",
        "http_route": "/api/v1/orders/:order",
        "http_request_method": "GET",
    },
)
SERVICE = Entity(
    kind=EntityKind.SERVICE,
    key="service|job=checkout-api",
    display_name="checkout-api",
    labels={"job": "checkout-api"},
)

CPU_QUERY = f'1 - avg by (job, instance) (rate(node_cpu_seconds_total{{{SEL}, mode="idle"}}[5m]))'
HTTP_404_QUERY = (
    "sum by (job, http_route, http_request_method) (rate(http_server_request_duration_seconds_count"
    f'{{{SEL}, job="checkout-api", http_route="/api/v1/orders/:order", '
    'http_request_method="GET", http_response_status_code="404"}[5m]))'
)

ALL_FAMILIES = list(SignalFamily)


def analysis_key(analysis_id: str) -> str:
    scope = ",".join(f"{m.name}={m.value}" for m in SCOPE.matchers)
    return f"{analysis_id}|{scope}|{T.isoformat()}|{CONFIG.config_hash}"


# --- capabilities -----------------------------------------------------------------------------


def capabilities(
    history_days: float | None, histogram: bool, containers: bool = True
) -> list[MetricCapability]:
    """Capability manifest derived from supplied examples; nothing here is live-verified."""
    unverified = "Assumed from owner-supplied examples; not observed in the live source (T003)."
    return [
        MetricCapability(
            family=SignalFamily.CPU,
            signal="cpu_utilization",
            status=CapabilityStatus.UNVERIFIED,
            verified=False,
            required_metrics=["node_cpu_seconds_total"],
            required_labels=["project", "env", "job", "instance", "mode"],
            observed_metrics=["node_cpu_seconds_total"],
            reason=unverified,
            history_days=history_days,
        ),
        *[
            MetricCapability(
                family=family,
                signal=signal,
                status=CapabilityStatus.UNVERIFIED,
                verified=False,
                required_metrics=metrics,
                required_labels=["project", "env", "job", "instance"],
                reason="Standard node exporter metric; not supplied or observed yet (T003).",
            )
            for family, signal, metrics in (
                (
                    SignalFamily.MEMORY,
                    "memory_utilization",
                    ["node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes"],
                ),
                (
                    SignalFamily.FILESYSTEM,
                    "filesystem_used_ratio",
                    ["node_filesystem_avail_bytes", "node_filesystem_size_bytes"],
                ),
                (SignalFamily.DISK_IO, "disk_busy_ratio", ["node_disk_io_time_seconds_total"]),
                (
                    SignalFamily.NETWORK,
                    "network_bytes",
                    ["node_network_receive_bytes_total", "node_network_transmit_bytes_total"],
                ),
            )
        ],
        MetricCapability(
            family=SignalFamily.CONTAINER,
            signal="container_cpu",
            status=CapabilityStatus.UNVERIFIED if containers else CapabilityStatus.UNSUPPORTED,
            verified=False,
            required_metrics=["container_cpu_usage_seconds_total"],
            required_labels=["project", "env"],
            reason=(
                "cAdvisor metric names and container identity labels need discovery (T003)."
                if containers
                else "No cAdvisor container metrics in this scope."
            ),
        ),
        *[
            MetricCapability(
                family=family,
                signal=signal,
                status=CapabilityStatus.UNVERIFIED,
                verified=False,
                required_metrics=["http_server_request_duration_seconds_count"],
                required_labels=[
                    "project",
                    "env",
                    "job",
                    "http_route",
                    "http_request_method",
                    "http_response_status_code",
                ],
                observed_metrics=["http_server_request_duration_seconds_count"],
                reason=unverified,
                history_days=history_days,
            )
            for family, signal in (
                (SignalFamily.REQUEST_TRAFFIC, "request_rate"),
                (SignalFamily.REQUEST_FAILURES, "server_error_ratio"),
                (SignalFamily.CLIENT_ERRORS, "client_error_rate"),
            )
        ],
        MetricCapability(
            family=SignalFamily.LATENCY,
            signal="latency_quantile",
            status=CapabilityStatus.UNVERIFIED if histogram else CapabilityStatus.UNSUPPORTED,
            verified=False,
            required_metrics=["http_server_request_duration_seconds_bucket"],
            required_labels=["project", "env", "job", "le"],
            reason=(
                "Histogram buckets not yet verified (T003)."
                if histogram
                else "No histogram buckets or _sum for http_server_request_duration_seconds; "
                "count-only data does not support latency."
            ),
        ),
    ]


def observed_capabilities() -> list[MetricCapability]:
    """shop/production capabilities verified live by T003 (docs/telemetry-inventory.md §4)."""
    history = 28.4
    http_labels = [
        "project",
        "env",
        "job",
        "instance",
        "http_route",
        "http_request_method",
        "http_response_status_code",
    ]
    rpc_labels = ["project", "env", "job", "instance", "rpc_method", "rpc_response_status_code"]
    node_labels = ["project", "env", "job", "instance"]

    def cap(
        family: SignalFamily,
        signal: str,
        status: CapabilityStatus,
        metrics: list[str],
        labels: list[str],
        reason: str | None = None,
    ) -> MetricCapability:
        observed = [] if status is CapabilityStatus.UNSUPPORTED else metrics
        return MetricCapability(
            family=family,
            signal=signal,
            status=status,
            verified=True,
            required_metrics=metrics,
            required_labels=labels,
            observed_metrics=observed,
            reason=reason,
            history_days=None if status is CapabilityStatus.UNSUPPORTED else history,
        )

    ok, partial, unsupported = (
        CapabilityStatus.SUPPORTED,
        CapabilityStatus.PARTIAL,
        CapabilityStatus.UNSUPPORTED,
    )
    return [
        cap(
            SignalFamily.CPU,
            "cpu_utilization",
            ok,
            ["node_cpu_seconds_total"],
            [*node_labels, "mode"],
        ),
        cap(SignalFamily.CPU, "cpu_iowait", ok, ["node_cpu_seconds_total"], [*node_labels, "mode"]),
        cap(
            SignalFamily.MEMORY,
            "memory_utilization",
            ok,
            ["node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes"],
            node_labels,
        ),
        cap(
            SignalFamily.FILESYSTEM,
            "filesystem_used_ratio",
            ok,
            ["node_filesystem_avail_bytes", "node_filesystem_size_bytes"],
            [*node_labels, "device", "mountpoint", "fstype"],
            "ext4 only by default; tmpfs, nfs4, overlay excluded.",
        ),
        cap(
            SignalFamily.FILESYSTEM,
            "filesystem_inodes_used_ratio",
            ok,
            ["node_filesystem_files", "node_filesystem_files_free"],
            [*node_labels, "device", "mountpoint", "fstype"],
        ),
        cap(
            SignalFamily.DISK_IO,
            "disk_busy_ratio",
            ok,
            ["node_disk_io_time_seconds_total"],
            [*node_labels, "device"],
            "sr0 excluded.",
        ),
        cap(
            SignalFamily.NETWORK,
            "network_bytes",
            ok,
            ["node_network_receive_bytes_total", "node_network_transmit_bytes_total"],
            [*node_labels, "device"],
            "lo excluded.",
        ),
        cap(
            SignalFamily.NETWORK,
            "network_errors",
            ok,
            [
                "node_network_receive_errs_total",
                "node_network_transmit_errs_total",
                "node_network_receive_drop_total",
                "node_network_transmit_drop_total",
            ],
            [*node_labels, "device"],
        ),
        cap(
            SignalFamily.CONTAINER,
            "container_cpu",
            partial,
            ["container_cpu_usage_seconds_total"],
            [*node_labels, "name", "id"],
            "Named containers on 3 of 4 hosts; shop-production-4 exposes only the root cgroup.",
        ),
        cap(
            SignalFamily.CONTAINER,
            "container_memory_working_set",
            partial,
            ["container_memory_working_set_bytes"],
            [*node_labels, "name", "id"],
            "Same host coverage as container_cpu.",
        ),
        cap(
            SignalFamily.CONTAINER,
            "container_memory_limit_ratio",
            unsupported,
            ["container_spec_memory_limit_bytes"],
            [*node_labels, "name"],
            "container_spec_memory_limit_bytes is 0 for every container (no limits set).",
        ),
        cap(
            SignalFamily.CONTAINER,
            "container_throttling_ratio",
            unsupported,
            ["container_cpu_cfs_throttled_periods_total", "container_cpu_cfs_periods_total"],
            [*node_labels, "name"],
            "No container_cpu_cfs_* metrics exported.",
        ),
        cap(
            SignalFamily.CONTAINER,
            "container_oom",
            ok,
            ["container_oom_events_total"],
            [*node_labels, "name"],
        ),
        cap(
            SignalFamily.CONTAINER,
            "container_restarts",
            ok,
            [
                "docker_swarm_task_info",
                "docker_swarm_service_replicas_running",
                "container_start_time_seconds",
            ],
            ["project", "env", "service_name", "node_hostname"],
            "Detect changes; exit-code series include historical tasks.",
        ),
        cap(
            SignalFamily.REQUEST_TRAFFIC,
            "request_rate",
            ok,
            ["http_server_request_duration_seconds_count"],
            http_labels,
        ),
        cap(
            SignalFamily.REQUEST_TRAFFIC,
            "rpc_request_rate",
            ok,
            ["rpc_server_call_duration_seconds_count"],
            rpc_labels,
        ),
        cap(
            SignalFamily.REQUEST_FAILURES,
            "server_error_ratio",
            ok,
            ["http_server_request_duration_seconds_count"],
            http_labels,
            "5xx extremely rare (2 in 27 days); classify by http_response_status_code only.",
        ),
        cap(
            SignalFamily.REQUEST_FAILURES,
            "rpc_error_ratio",
            ok,
            ["rpc_server_call_duration_seconds_count"],
            rpc_labels,
            'Failure = rpc_response_status_code != "OK".',
        ),
        cap(
            SignalFamily.CLIENT_ERRORS,
            "client_error_rate",
            ok,
            ["http_server_request_duration_seconds_count"],
            http_labels,
        ),
        cap(
            SignalFamily.LATENCY,
            "latency_quantile",
            ok,
            ["http_server_request_duration_seconds_bucket"],
            [*http_labels, "le"],
            "Classic buckets 0.005-10 s; quantiles above 10 s are not resolvable.",
        ),
        cap(
            SignalFamily.LATENCY,
            "rpc_latency_quantile",
            ok,
            ["rpc_server_call_duration_seconds_bucket"],
            [*rpc_labels, "le"],
            "Classic buckets 0.005-10 s.",
        ),
    ]


# --- series and evidence ----------------------------------------------------------------------


def make_series(
    *,
    family: SignalFamily,
    signal: str,
    entity: Entity,
    unit: Unit,
    query: str,
    start: datetime,
    values: list[float | None],
    extra_labels: dict[str, str] | None = None,
) -> MetricSeries:
    labels = {**SCOPE_LABELS, **entity.labels, **(extra_labels or {})}
    coverage = sum(v is not None for v in values) / len(values) if values else 0.0
    return MetricSeries(
        series_id=series_id(query, entity.key),
        family=family,
        signal=signal,
        entity=entity,
        labels=labels,
        unit=unit,
        query=query,
        step_seconds=STEP_SECONDS,
        start=start,
        values=values,
        coverage=coverage,
    )


def wave(n: int, start: datetime, base: float, amp: float, noise: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        hour = (start + i * STEP).hour + (start + i * STEP).minute / 60
        out.append(base + amp * math.sin((hour - 8) / 24 * 2 * math.pi) + rng.gauss(0, noise))
    return out


def cpu_finding(
    aid: str,
    confidence: ConfidenceLevel,
    reasons: list[Reason],
    baseline_days: int,
    related: list[str],
) -> tuple[Finding, Evidence]:
    start, end = T - timedelta(hours=5), T - timedelta(hours=2)
    fid = finding_id(analysis_key(aid), NODE.key, "cpu_utilization", format_utc(start))
    ev_start = start - timedelta(hours=6)
    n = int((min(end + timedelta(hours=6), T) - ev_start) / STEP)
    values: list[float | None] = []
    for i, v in enumerate(wave(n, ev_start, 0.22, 0.05, 0.015, seed=11)):
        ts = ev_start + i * STEP
        values.append(round(0.95 + (v - 0.22) * 0.1, 4) if start <= ts < end else round(v, 4))
    series = make_series(
        family=SignalFamily.CPU,
        signal="cpu_utilization",
        entity=NODE,
        unit=Unit.RATIO,
        query=CPU_QUERY,
        start=ev_start,
        values=values,
    )
    eid = evidence_id(fid, series.series_id)
    finding = Finding(
        finding_id=fid,
        detector="robust_baseline",
        detector_version=CONFIG.version,
        method=DetectionMethod.RELATIVE,
        family=SignalFamily.CPU,
        signal="cpu_utilization",
        title="CPU utilisation above expected range",
        entity=NODE,
        start=start,
        end=end,
        peak_at=start + timedelta(minutes=95),
        duration_seconds=int((end - start).total_seconds()),
        severity=Severity.CRITICAL,
        severity_points=5,
        confidence=confidence,
        confidence_reasons=reasons,
        observed=ObservedValue(value=0.963, unit=Unit.RATIO),
        expected=ExpectedValue(median=0.22, lower=0.16, upper=0.28, unit=Unit.RATIO),
        peak_score=24.8,
        threshold=None,
        baseline_days=baseline_days,
        baseline_mode=BaselineMode.TIME_OF_DAY
        if baseline_days >= 7
        else BaselineMode.WHOLE_BASELINE,
        evidence_ids=[eid],
        related_finding_ids=related,
    )
    evidence = Evidence(
        evidence_id=eid,
        finding_id=fid,
        series=series,
        expected=[0.22] * n,
        lower=[0.16] * n,
        upper=[0.28] * n,
        threshold=0.90,
        threshold_label="Diagnostic heuristic: 90 % for ≥ 15 min (not an SLO)",
    )
    return finding, evidence


def not_found_finding(aid: str) -> tuple[Finding, Evidence]:
    start, end = T - timedelta(hours=9), T - timedelta(hours=7, minutes=30)
    fid = finding_id(analysis_key(aid), ROUTE.key, "client_error_rate", format_utc(start))
    ev_start = start - timedelta(hours=6)
    n = int((end + timedelta(hours=6) - ev_start) / STEP)
    values: list[float | None] = []
    for i, v in enumerate(wave(n, ev_start, 0.8, 0.3, 0.08, seed=23)):
        ts = ev_start + i * STEP
        values.append(round(v * 6 if start <= ts < end else v, 4))
    # A short scrape gap: gaps are None, never zero.
    for i in range(40, 43):
        values[i] = None
    series = make_series(
        family=SignalFamily.CLIENT_ERRORS,
        signal="client_error_rate",
        entity=ROUTE,
        unit=Unit.REQUESTS_PER_SECOND,
        query=HTTP_404_QUERY,
        start=ev_start,
        values=values,
        extra_labels={"http_response_status_code": "404"},
    )
    eid = evidence_id(fid, series.series_id)
    finding = Finding(
        finding_id=fid,
        detector="robust_baseline",
        detector_version=CONFIG.version,
        method=DetectionMethod.RELATIVE,
        family=SignalFamily.CLIENT_ERRORS,
        signal="client_error_rate",
        title="404 increase",
        entity=ROUTE,
        start=start,
        end=end,
        peak_at=start + timedelta(minutes=40),
        duration_seconds=int((end - start).total_seconds()),
        severity=Severity.MEDIUM,
        severity_points=3,
        confidence=ConfidenceLevel.MEDIUM,
        confidence_reasons=[
            Reason(code="coverage_reduced", message="Coverage 96 % in episode ± 1 h (scrape gap)."),
            Reason(code="request_volume", message="30-300 requests per step window."),
        ],
        observed=ObservedValue(value=5.9, unit=Unit.REQUESTS_PER_SECOND),
        expected=ExpectedValue(median=0.8, lower=0.4, upper=1.3, unit=Unit.REQUESTS_PER_SECOND),
        peak_score=11.2,
        threshold=None,
        baseline_days=14,
        baseline_mode=BaselineMode.TIME_OF_DAY,
        evidence_ids=[eid],
        attributes={"http_response_status_code": "404", "error_type": "404"},
        recurrence=Recurrence.RECURRING,
        prior_episode_days=3,
    )
    evidence = Evidence(
        evidence_id=eid,
        finding_id=fid,
        series=series,
        expected=[0.8] * n,
        lower=[0.4] * n,
        upper=[1.3] * n,
    )
    return finding, evidence


# --- trends and coverage ----------------------------------------------------------------------


def trends(
    aid: str,
    *,
    history_days: int,
    findings: list[Finding],
    recurring_404_days: tuple[int, ...] = (),
) -> list[DailyTrend]:
    out = []
    entities = 2  # node + checkout-api route in the synthetic scope
    for i in range(TREND_DAYS):
        end = T - timedelta(days=i)
        start = end - timedelta(days=1)
        preceding = max(0, min(CONFIG.baseline_max_days, history_days - (i + 1)))
        observed_days = history_days > i
        episodes: list[EpisodeSummary] = []
        if i == 0:
            for f in findings:
                episodes.append(
                    EpisodeSummary(
                        episode_id=episode_id(
                            analysis_key(aid), f.entity.key, f.signal, format_utc(f.start)
                        ),
                        finding_id=f.finding_id,
                        entity=f.entity,
                        family=f.family,
                        signal=f.signal,
                        start=f.start,
                        end=f.end,
                        severity=f.severity,
                        peak_observed=f.observed.value,
                        expected_median=f.expected.median if f.expected else None,
                        unit=f.observed.unit,
                    )
                )
        elif i in recurring_404_days and preceding >= CONFIG.baseline_min_adequate_days:
            s = start + timedelta(hours=14)
            episodes.append(
                EpisodeSummary(
                    episode_id=episode_id(
                        analysis_key(aid), ROUTE.key, "client_error_rate", format_utc(s)
                    ),
                    finding_id=None,
                    entity=ROUTE,
                    family=SignalFamily.CLIENT_ERRORS,
                    signal="client_error_rate",
                    start=s,
                    end=s + timedelta(minutes=45),
                    severity=Severity.LOW,
                    peak_observed=3.1,
                    expected_median=0.8,
                    unit=Unit.REQUESTS_PER_SECOND,
                )
            )
        minutes = sum(int((e.end - e.start).total_seconds() // 60) for e in episodes)
        observed = entities * 24 * 60 if observed_days else 0
        if not observed_days:
            status = TrendBucketStatus.INSUFFICIENT_DATA
        elif preceding < CONFIG.baseline_min_adequate_days:
            status = TrendBucketStatus.INSUFFICIENT_BASELINE
        else:
            status = TrendBucketStatus.OK
        severities = [e.severity for e in episodes]
        order = list(Severity)
        out.append(
            DailyTrend(
                bucket_index=i,
                window=TimeRange(start=start, end=end),
                status=status,
                episode_count=len(episodes),
                anomalous_minutes=minutes,
                peak_severity=max(severities, key=order.index) if severities else None,
                affected_entities=sorted({e.entity.key for e in episodes}),
                observed_entity_minutes=observed,
                anomalous_share=round(minutes / observed, 6) if observed else None,
                baseline_days_used=preceding,
                coverage=1.0 if observed_days else 0.0,
                episodes=episodes,
            )
        )
    return out


def coverage(
    findings: list[Finding],
    baseline_days: int,
    histogram: bool,
    source_error: SignalFamily | None = None,
) -> list[SignalCoverage]:
    anomalous = {f.family for f in findings}
    rows = []
    for family in ALL_FAMILIES:
        if family is SignalFamily.LATENCY and not histogram:
            rows.append(
                SignalCoverage(
                    family=family,
                    status=SignalStatus.UNSUPPORTED,
                    capability=CapabilityStatus.UNSUPPORTED,
                    evaluated_series=0,
                    total_series=0,
                    reasons=[
                        Reason(
                            code="histogram_missing",
                            message="No histogram buckets for "
                            "http_server_request_duration_seconds.",
                        )
                    ],
                )
            )
        elif family is SignalFamily.CONTAINER:
            rows.append(
                SignalCoverage(
                    family=family,
                    status=SignalStatus.UNSUPPORTED,
                    capability=CapabilityStatus.UNSUPPORTED,
                    evaluated_series=0,
                    total_series=0,
                    reasons=[
                        Reason(
                            code="metrics_absent",
                            message="No cAdvisor container metrics in this scope.",
                        )
                    ],
                )
            )
        elif family is source_error:
            rows.append(
                SignalCoverage(
                    family=family,
                    status=SignalStatus.SOURCE_ERROR,
                    capability=CapabilityStatus.UNVERIFIED,
                    evaluated_series=0,
                    total_series=4,
                    reasons=[Reason(code="query_timeout", message="Query timed out after 30 s.")],
                )
            )
        elif baseline_days < CONFIG.baseline_min_adequate_days and family not in anomalous:
            rows.append(
                SignalCoverage(
                    family=family,
                    status=SignalStatus.INSUFFICIENT_DATA,
                    capability=CapabilityStatus.UNVERIFIED,
                    evaluated_series=2,
                    total_series=2,
                    baseline_days=baseline_days,
                    reasons=[
                        Reason(
                            code="insufficient_baseline",
                            message=f"{baseline_days} baseline days (< 3); "
                            "relative detection unavailable, absolute checks ran.",
                        )
                    ],
                )
            )
        else:
            rows.append(
                SignalCoverage(
                    family=family,
                    status=SignalStatus.ANOMALOUS
                    if family in anomalous
                    else SignalStatus.NO_ANOMALY,
                    capability=CapabilityStatus.UNVERIFIED,
                    evaluated_series=2,
                    total_series=2,
                    baseline_days=baseline_days,
                )
            )
    return rows


# --- reports ----------------------------------------------------------------------------------


def report(
    aid: str,
    *,
    state: ReportState,
    findings: list[Finding],
    evidence: list[Evidence],
    history_days: int,
    histogram: bool,
    explanation: ExplanationResult,
    exclusions: list[Exclusion] | None = None,
    source_error: SignalFamily | None = None,
    recurring: tuple[int, ...] = (),
) -> AnalysisReport:
    baseline_days = min(CONFIG.baseline_max_days, history_days - 1)
    daily = trends(aid, history_days=history_days, findings=findings, recurring_404_days=recurring)
    return AnalysisReport(
        analysis_id=aid,
        scope=SCOPE,
        windows=WINDOWS,
        generated_at=T + timedelta(minutes=2),
        detector_version=CONFIG.version,
        config_hash=CONFIG.config_hash,
        source=SOURCE,
        state=state,
        capabilities=capabilities(float(history_days), histogram, containers=False),
        coverage=coverage(findings, baseline_days, histogram, source_error),
        findings=findings,
        trend_summary=trend_summary(daily),
        trends=daily,
        evidence=evidence,
        exclusions=exclusions or [],
        explanation=explanation,
    )


def ai_success(findings: list[Finding]) -> ExplanationResult:
    cpu, nf = findings
    return ExplanationResult(
        status=ExplanationStatus.SUCCEEDED,
        explanation=Explanation(
            summary="Sustained CPU saturation on shop-production for 3 h and an earlier, separate "
            "increase of 404 responses on checkout-api GET /api/v1/orders/:order.",
            hypotheses=[
                Hypothesis(
                    text="A batch or runaway process saturated CPU on shop-production.",
                    finding_ids=[cpu.finding_id],
                    likelihood=Likelihood.PLAUSIBLE,
                ),
                Hypothesis(
                    text="A client is polling for task IDs that no longer exist.",
                    finding_ids=[nf.finding_id],
                    likelihood=Likelihood.POSSIBLE,
                ),
            ],
            investigation_steps=[
                InvestigationStep(
                    text="List top CPU consumers on shop-production for 05:05-08:05 UTC.",
                    finding_ids=[cpu.finding_id],
                ),
                InvestigationStep(
                    text="Group 404 requests on /api/v1/orders/:order by client.",
                    finding_ids=[nf.finding_id],
                ),
            ],
            uncertainty="No established mapping between node shop-production and checkout-api "
            "instances; the two findings are treated as unrelated.",
            provider="openai",
            model="configured-model",
            generated_at=T + timedelta(minutes=2),
        ),
    )


def job(
    aid: str,
    state: JobState,
    *,
    stage_at: StageName | None = None,
    error: JobError | None = None,
    report_available: bool = False,
    explanation: ExplanationStatus = ExplanationStatus.PENDING,
    counts: dict[Severity, int] | None = None,
) -> AnalysisJob:
    stages = []
    reached = True
    for name in StageName:
        if name == stage_at:
            status = StageStatus.RUNNING if state is JobState.RUNNING else StageStatus.FAILED
            reached = False
        elif (reached and stage_at is not None) or (
            stage_at is None and state is JobState.COMPLETED
        ):
            status = StageStatus.DONE
        else:
            status = StageStatus.PENDING
        stages.append(
            StageProgress(
                stage=name,
                status=status,
                done=38 if name is StageName.COLLECTION and status is StageStatus.RUNNING else None,
                total=52
                if name is StageName.COLLECTION and status is StageStatus.RUNNING
                else None,
            )
        )
    return AnalysisJob(
        analysis_id=aid,
        scope=SCOPE,
        end_time=T,
        detector_version=CONFIG.version,
        config_hash=CONFIG.config_hash,
        state=state,
        stages=stages,
        explanation_status=explanation,
        error=error,
        created_at=T,
        started_at=T + timedelta(seconds=1),
        finished_at=None if state.active else T + timedelta(minutes=2),
        report_available=report_available,
        finding_counts=counts,
    )


def build() -> dict[str, BaseModel]:
    ids = {
        "healthy": "01999a2b-0000-7000-8000-000000000001",
        "anomalies": "01999a2b-0000-7000-8000-000000000002",
        "ai_failed": "01999a2b-0000-7000-8000-000000000003",
        "short_history": "01999a2b-0000-7000-8000-000000000004",
        "partial": "01999a2b-0000-7000-8000-000000000005",
        "running": "01999a2b-0000-7000-8000-000000000006",
        "interrupted": "01999a2b-0000-7000-8000-000000000007",
    }
    full_reasons: list[Reason] = []

    cpu, cpu_ev = cpu_finding(ids["anomalies"], ConfidenceLevel.HIGH, full_reasons, 14, [])
    nf, nf_ev = not_found_finding(ids["anomalies"])
    anomalies = report(
        ids["anomalies"],
        state=ReportState.COMPLETED,
        findings=[cpu, nf],
        evidence=[cpu_ev, nf_ev],
        history_days=28,
        histogram=True,
        explanation=ai_success([cpu, nf]),
        recurring=(3, 5, 9),
    )

    cpu2, cpu2_ev = cpu_finding(ids["ai_failed"], ConfidenceLevel.HIGH, full_reasons, 14, [])
    nf2, nf2_ev = not_found_finding(ids["ai_failed"])
    ai_failed = report(
        ids["ai_failed"],
        state=ReportState.COMPLETED,
        findings=[cpu2, nf2],
        evidence=[cpu2_ev, nf2_ev],
        history_days=28,
        histogram=True,
        explanation=ExplanationResult(
            status=ExplanationStatus.FAILED, reason="timeout after 60 s (1 retry)"
        ),
    )

    cpu3, cpu3_ev = cpu_finding(
        ids["short_history"],
        ConfidenceLevel.MEDIUM,
        [Reason(code="baseline_short", message="4 adequate baseline days (< 7).")],
        4,
        [],
    )
    short = report(
        ids["short_history"],
        state=ReportState.COMPLETED,
        findings=[cpu3],
        evidence=[cpu3_ev],
        history_days=5,
        histogram=False,
        explanation=ExplanationResult(status=ExplanationStatus.DISABLED, reason="AI_PROVIDER=none"),
    )

    healthy = report(
        ids["healthy"],
        state=ReportState.COMPLETED,
        findings=[],
        evidence=[],
        history_days=28,
        histogram=True,
        explanation=ExplanationResult(status=ExplanationStatus.SKIPPED_NO_FINDINGS),
    )

    partial = report(
        ids["partial"],
        state=ReportState.PARTIAL,
        findings=[],
        evidence=[],
        history_days=28,
        histogram=True,
        source_error=SignalFamily.NETWORK,
        explanation=ExplanationResult(
            status=ExplanationStatus.NOT_CONFIGURED, reason="Set OPENAI_API_KEY or AI_PROVIDER=none"
        ),
        exclusions=[
            Exclusion(
                code="query_timeout",
                family=SignalFamily.NETWORK,
                message="Network queries timed out after 30 s; not evaluated.",
            )
        ],
    )

    counts = {Severity.CRITICAL: 1, Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 0}
    completed = job(
        ids["anomalies"],
        JobState.COMPLETED,
        report_available=True,
        explanation=ExplanationStatus.SUCCEEDED,
        counts=counts,
    ).model_copy(update={"daily_episodes": daily_episodes(anomalies)})
    return {
        "reports/report_healthy.json": healthy,
        "reports/report_anomalies.json": anomalies,
        "reports/report_ai_failed.json": ai_failed,
        "reports/report_short_history.json": short,
        "reports/report_partial_source_error.json": partial,
        "reports/report_cloudflare.json": cloudflare_report(),
        "reports/report_sentry.json": sentry_report(),
        "reports/report_wazuh.json": wazuh_report(),
        "jobs/job_running.json": job(
            ids["running"], JobState.RUNNING, stage_at=StageName.COLLECTION
        ),
        "jobs/job_completed.json": completed,
        "jobs/job_interrupted.json": job(
            ids["interrupted"],
            JobState.FAILED,
            stage_at=StageName.COLLECTION,
            error=JobError(
                code=ErrorCode.INTERRUPTED_BY_RESTART,
                message="The service restarted while this analysis was running. "
                "No report was saved.",
            ),
        ),
        "jobs/submitted_duplicate.json": AnalysisSubmitted(
            analysis=job(ids["running"], JobState.RUNNING, stage_at=StageName.COLLECTION),
            duplicate_of_active=True,
        ),
        "api/config.json": RuntimeConfig(
            version="0.1.0",
            ai_provider="openai",
            ai_model=None,
            explanation_status=ExplanationStatus.NOT_CONFIGURED,
            detector_version=CONFIG.version,
            config_hash=CONFIG.config_hash,
            limits=Limits(
                max_running_jobs=1,
                max_queued_jobs=4,
                query_timeout_seconds=30,
                max_series_per_query=500,
                max_series_per_job=5000,
                report_max_bytes=20 * 1024 * 1024,
            ),
        ),
        "api/projects.json": ProjectList(
            items=[
                ProjectSummary(
                    project_id=PROJECT_ID,
                    name=SCOPE.project_name,
                    description="Synthetic example project.",
                    matchers=SCOPE.matchers,
                    sources=[
                        PrometheusSource(
                            url="http://victoriametrics.example:8428",
                            tls_verify=True,
                            auth=BearerAuth(token_set=True),
                        )
                    ],
                    schedule=SCHEDULE,
                    next_scheduled_run=SCHEDULE.next_after(T),
                    created_at=T,
                    updated_at=T,
                    latest_analysis=completed,
                    report_count=1,
                )
            ]
        ),
        "api/connection_test.json": ConnectionTest(
            reachable=True,
            auth_ok=True,
            matched_series=1832,
            history_days=30.0,
            families=[
                FamilyCapability(family=SignalFamily.CPU, status=CapabilityStatus.SUPPORTED),
                FamilyCapability(
                    family=SignalFamily.LATENCY,
                    status=CapabilityStatus.UNSUPPORTED,
                    reason="No histogram buckets for http_server_request_duration_seconds.",
                ),
            ],
            checked_at=T,
        ),
        "api/connection_test_cloudflare.json": cloudflare_connection_test(),
        "api/connection_test_sentry.json": sentry_connection_test(),
        "api/connection_test_wazuh.json": wazuh_connection_test(),
        "api/problem_queue_full.json": Problem(
            title="Analysis queue is full",
            status=429,
            code=ErrorCode.QUEUE_FULL,
            detail="1 analysis running and 4 queued; retry later.",
        ),
        "api/problem_report_not_ready.json": Problem(
            title="Report not ready",
            status=409,
            code=ErrorCode.REPORT_NOT_READY,
            detail="Analysis is still running (collection).",
        ),
        "metrics/capabilities_supplied_unverified.json": _Manifest(
            items=capabilities(history_days=None, histogram=True)
        ),
        "metrics/capabilities_shop_production_observed.json": _Manifest(
            items=observed_capabilities()
        ),
    }


class _Manifest(BaseModel):
    items: list[MetricCapability]


CLOUDFLARE_ZONE = "0123456789abcdef0123456789abcdef"
CLOUDFLARE_ANALYSIS_ID = "01999a3c-0000-7000-8000-0000000000cf"


class _NoProgress:
    async def update(self, progress: StageProgress) -> None:
        pass


def cloudflare_report() -> AnalysisReport:
    """A Cloudflare-only project analysed from the synthetic incident scenario (T015)."""
    api = SyntheticCloudflareApi("incident", CLOUDFLARE_ZONE, ["shop.example.com"])
    source = CloudflareMetricsSource(api, CLOUDFLARE_ZONE, ["shop.example.com"])
    project = Scope(
        project_id="01999a3c-0000-7000-8000-0000000000c1", project_name="Shop edge", matchers=[]
    )
    return pipeline_report(
        OpenedSource(SourceKind.CLOUDFLARE, source), project, CLOUDFLARE_ANALYSIS_ID
    )


SENTRY_ANALYSIS_ID = "01999a3c-0000-7000-8000-0000000000e5"


def sentry_report() -> AnalysisReport:
    """A Sentry-only project analysed from the synthetic incident scenario (T017)."""
    projects, tags = ["shop-api", "shop-web"], [SentryTag(key="team", value="shop")]
    api = SyntheticSentryApi("incident", "acme", projects, "production", tags)
    source = SentryMetricsSource(api, "acme", projects, "production", tags)
    project = Scope(
        project_id="01999a3c-0000-7000-8000-0000000000e1", project_name="Shop app", matchers=[]
    )
    return pipeline_report(OpenedSource(SourceKind.SENTRY, source), project, SENTRY_ANALYSIS_ID)


WAZUH_ANALYSIS_ID = "01999a3c-0000-7000-8000-0000000000e6"


def wazuh_report() -> AnalysisReport:
    """A Wazuh-only project analysed from the synthetic incident scenario (T018)."""
    labels = [WazuhLabel(key="project", value="shop")]
    api = SyntheticWazuhApi("incident", "wazuh-alerts-4.x-*", [], labels)
    source = WazuhMetricsSource(api, "wazuh-alerts-4.x-*", [], labels)
    project = Scope(
        project_id="01999a3c-0000-7000-8000-0000000000e1", project_name="Shop hosts", matchers=[]
    )
    return pipeline_report(OpenedSource(SourceKind.WAZUH, source), project, WAZUH_ANALYSIS_ID)


def pipeline_report(opened: OpenedSource, scope: Scope, analysis_id: str) -> AnalysisReport:
    """A report produced by the real pipeline from one source, with pinned timestamps."""
    config = DetectorConfig()

    class Sources:
        @asynccontextmanager
        async def open(self, scope: Scope) -> AsyncIterator[list[OpenedSource]]:
            yield [opened]

    pipeline = AnalysisPipeline(Sources(), RobustDetector(), config, FakeExplanationProvider())
    request = AnalysisRequest(
        scope=scope,
        end_time=T,
        detector_version=config.version,
        config_hash=config.config_hash,
    )
    report = asyncio.run(pipeline.run(analysis_id, request, _NoProgress(), CancellationToken()))
    explanation = report.explanation
    if explanation.explanation is not None:  # pin the provider's timestamp
        pinned = explanation.explanation.model_copy(update={"generated_at": T})
        explanation = explanation.model_copy(update={"explanation": pinned})
    return report.model_copy(update={"generated_at": T, "explanation": explanation})


def cloudflare_connection_test() -> ConnectionTest:
    return ConnectionTest(
        kind=SourceKind.CLOUDFLARE,
        reachable=True,
        auth_ok=True,
        matched_series=3_456_789,
        history_days=30.0,
        families=[
            FamilyCapability(family=SignalFamily.EDGE, status=CapabilityStatus.SUPPORTED),
            FamilyCapability(family=SignalFamily.SECURITY, status=CapabilityStatus.SUPPORTED),
        ],
        message="3,456,789 requests in the last 24 h.",
        checked_at=T,
    )


def sentry_connection_test() -> ConnectionTest:
    return ConnectionTest(
        kind=SourceKind.SENTRY,
        reachable=True,
        auth_ok=True,
        matched_series=1_284_310,
        history_days=30.0,
        families=[
            FamilyCapability(family=SignalFamily.APP_ERRORS, status=CapabilityStatus.SUPPORTED),
            FamilyCapability(
                family=SignalFamily.APP_PERFORMANCE, status=CapabilityStatus.SUPPORTED
            ),
        ],
        message="1,842 error events and 1,282,468 transactions in 2 projects (production) "
        "in the last 24 h.",
        checked_at=T,
    )


def wazuh_connection_test() -> ConnectionTest:
    return ConnectionTest(
        kind=SourceKind.WAZUH,
        reachable=True,
        auth_ok=True,
        matched_series=8_412,
        history_days=28.0,
        families=[
            FamilyCapability(family=SignalFamily.HOST_SECURITY, status=CapabilityStatus.SUPPORTED),
            FamilyCapability(family=SignalFamily.FILE_INTEGRITY, status=CapabilityStatus.SUPPORTED),
        ],
        message="8,412 alerts from 3 agents in the last 24 h.",
        checked_at=T,
    )


def render(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if fixtures are stale")
    args = parser.parse_args()

    stale = []
    for rel, model in build().items():
        path = ROOT / rel
        text = render(model)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    if stale:
        print(
            "Stale fixtures (run: uv run python -m scripts.generate_fixtures):",
            *stale,
            sep="\n  ",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
