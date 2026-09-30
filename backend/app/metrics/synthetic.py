"""Deterministic synthetic MetricsSource for tests, UI development, and offline demos.

Series follow the label conventions of the owner-supplied examples (project "paas", env
"production", node "paas-production", HTTP job "dispatcher-api"). Values are synthetic. The
source reports ``backend="synthetic"`` so reports can never be mistaken for live data.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta

import numpy as np

from app.domain.common import STEP_SECONDS, Scope, SignalFamily
from app.domain.ids import series_id
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    DiscoveredValues,
    EntityMapping,
    MappingKind,
    ProgressReporter,
)
from app.domain.jobs import StageName, StageProgress, StageStatus
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.report import AnalysisWindows, SourceInfo
from app.metrics.catalog import BY_SIGNAL, CATALOG
from app.metrics.source import HISTORY_DAYS, entity_for

DAY = 288
N = HISTORY_DAYS * DAY
LATEST = N - DAY

TASK_ROUTE = {
    "job": "dispatcher-api",
    "http_route": "/api/v3/tasks/:task",
    "http_request_method": "GET",
}
QUIET_ROUTE = {
    "job": "dispatcher-api",
    "http_route": "/internal/task-sla",
    "http_request_method": "GET",
}


@dataclass(frozen=True)
class Scenario:
    """Injected behaviour; anomalies apply to the latest day unless stated otherwise."""

    history_days: int = HISTORY_DAYS
    cpu_load: bool = False
    """paas-production CPU at 95 % for 3 h, ending 2 h before T."""
    memory_growth: bool = False
    disk_depletion: bool = False
    not_found_burst: bool = False
    server_error_burst: bool = False
    latency_shift: bool = False
    traffic_drop: bool = False
    gaps: bool = False
    """A 2 h scrape gap on paas-production during the latest day."""
    flat_memory: bool = False
    """paas-production-2 memory constant (zero MAD)."""
    new_host_days: int | None = None
    """paas-production-4 appears this many days before T (changing population)."""
    histogram: bool = True
    containers: bool = True
    recurring_404_days: tuple[int, ...] = ()
    """Trend buckets (1..13) with an extra 404 burst, for recurrence."""
    seed: int = 7
    extra: dict[str, float] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "healthy": Scenario(),
    "incident": Scenario(
        cpu_load=True, not_found_burst=True, server_error_burst=True, recurring_404_days=(3, 5, 9)
    ),
    "short-history": Scenario(history_days=5, cpu_load=True, histogram=False),
    "degraded": Scenario(
        memory_growth=True,
        disk_depletion=True,
        latency_shift=True,
        traffic_drop=True,
        gaps=True,
        containers=False,
    ),
}


class _Gen:
    def __init__(self, scenario: Scenario) -> None:
        self.sc = scenario
        self.rng = np.random.default_rng(scenario.seed)
        tod = (np.arange(N) % DAY) / DAY
        self.diurnal = np.sin(2 * np.pi * (tod - 0.35))

    def base(self, level: float, amp: float, noise: float) -> np.ndarray:
        return level + amp * self.diurnal + self.rng.normal(0, noise, N)

    def available(self, values: np.ndarray, start_day: int | None = None) -> np.ndarray:
        out = values.astype(np.float64)
        first = N - self.sc.history_days * DAY
        if start_day is not None:
            first = max(first, N - start_day * DAY)
        out[: max(0, first)] = np.nan
        return out


def build_series(
    scenario: Scenario, scope: Scope, windows: AnalysisWindows
) -> tuple[list[MetricSeries], list[EntityMapping]]:
    g = _Gen(scenario)
    start = windows.end_time - timedelta(days=HISTORY_DAYS)
    out: list[MetricSeries] = []

    def add(signal: str, labels: dict[str, str], values: np.ndarray) -> None:
        defn = BY_SIGNAL[signal]
        entity = entity_for(defn, labels)
        query = defn.query.render(scope)
        vals = [None if np.isnan(v) else round(float(v), 6) for v in values]
        out.append(
            MetricSeries(
                series_id=series_id(query, entity.key),
                family=defn.family,
                signal=signal,
                entity=entity,
                labels={"project": scope.project, "env": scope.env, **entity.labels},
                unit=defn.unit,
                query=query,
                step_seconds=STEP_SECONDS,
                start=start,
                values=vals,
                coverage=sum(v is not None for v in vals) / N,
            )
        )

    hosts = ["paas-production", "paas-production-2"]
    if scenario.new_host_days is not None:
        hosts.append("paas-production-4")
    for h in hosts:
        node = {"job": "node", "instance": h}
        start_day = scenario.new_host_days if h == "paas-production-4" else None
        cpu = np.clip(g.base(0.22, 0.05, 0.01), 0, 1)
        if scenario.cpu_load and h == "paas-production":
            cpu[N - 60 : N - 24] = 0.95 + g.rng.normal(0, 0.005, 36)
        if scenario.gaps and h == "paas-production":
            cpu[LATEST + 100 : LATEST + 124] = np.nan
        add("cpu_utilization", node, g.available(cpu, start_day))
        mem = g.base(0.40, 0.01, 0.005)
        if scenario.flat_memory and h == "paas-production-2":
            mem = np.full(N, 0.35)
        if scenario.memory_growth and h == "paas-production":
            mem[LATEST:] = np.linspace(0.40, 0.93, DAY)
        add("memory_utilization", node, g.available(mem, start_day))
        fs = 0.50 + np.linspace(0, 0.01, N) + g.rng.normal(0, 0.0005, N)
        if scenario.disk_depletion and h == "paas-production-2":
            fs[LATEST:] = np.linspace(0.51, 0.97, DAY)
        add(
            "filesystem_used_ratio",
            {"job": "node", "instance": h, "device": "/dev/sda1", "mountpoint": "/"},
            g.available(fs, start_day),
        )

    routes = [(TASK_ROUTE, 20.0), (QUIET_ROUTE, 0.05)]
    for labels, rps in routes:
        req = np.clip(g.base(rps, rps * 0.3, rps * 0.03), 0, None)
        if scenario.traffic_drop and labels is TASK_ROUTE:
            req[N - 48 :] = req[N - 48 :] * 0.1
        nf = np.clip(req * 0.04 + g.rng.normal(0, 0.02 * rps, N), 0, None)
        if labels is TASK_ROUTE:
            if scenario.not_found_burst:
                nf[N - 108 : N - 90] = req[N - 108 : N - 90] * 0.3
            for b in scenario.recurring_404_days:
                s = N - (b + 1) * DAY + 150
                nf[s : s + 9] = req[s : s + 9] * 0.2
        add("http_requests", labels, g.available(req))
        add("http_404", labels, g.available(nf))
        add("http_4xx", labels, g.available(nf + 0.01 * req))
        if scenario.server_error_burst and labels is TASK_ROUTE:
            err = np.full(N, np.nan)
            err[N - 30 : N - 18] = req[N - 30 : N - 18] * 0.12
            add("http_5xx", labels, err)
        if scenario.histogram:
            p95 = np.clip(g.base(0.08, 0.01, 0.004), 0.001, None)
            if scenario.latency_shift and labels is TASK_ROUTE:
                p95[N - 72 :] = 0.4 + g.rng.normal(0, 0.01, 72)
            add("http_latency_p95", labels, g.available(p95))
            add("http_latency_p99", labels, g.available(p95 * 1.6))
        else:
            add(
                "http_latency_mean",
                labels,
                g.available(np.clip(g.base(0.03, 0.005, 0.002), 0.001, None)),
            )
    if scenario.containers:
        for h in hosts[:2]:
            add(
                "container_cpu",
                {"instance": h, "container": "paas_dispatcher-api.1"},
                g.available(np.clip(g.base(0.3, 0.05, 0.01), 0, None)),
            )
    mappings = [
        EntityMapping(
            kind=MappingKind.OTEL_SERVICE,
            service="dispatcher-api",
            host="paas-production-2",
            observed_at=windows.end_time,
        ),
    ]
    return out, mappings


class SyntheticMetricsSource:
    def __init__(self, scenario: Scenario | str = "incident") -> None:
        self.scenario = SCENARIOS[scenario] if isinstance(scenario, str) else scenario

    def with_scenario(self, **changes: object) -> SyntheticMetricsSource:
        return SyntheticMetricsSource(replace(self.scenario, **changes))  # type: ignore[arg-type]

    async def source_info(self) -> SourceInfo:
        return SourceInfo(base_url="synthetic://fixtures", backend="synthetic", version=None)

    async def list_projects(self) -> DiscoveredValues:
        return DiscoveredValues(values=["paas"])

    async def list_envs(self, project: str) -> DiscoveredValues:
        return DiscoveredValues(values=["production"] if project == "paas" else [])

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        sc = self.scenario
        produced = {
            "cpu_utilization",
            "memory_utilization",
            "filesystem_used_ratio",
            "http_requests",
            "http_404",
            "http_4xx",
            "http_5xx",
            *(("http_latency_p95", "http_latency_p99") if sc.histogram else ("http_latency_mean",)),
            *(("container_cpu",) if sc.containers else ()),
        }
        caps = []
        for defn in CATALOG:
            supported = defn.signal in produced
            reason = None
            if not supported:
                reason = (
                    "No histogram buckets for http_server_request_duration_seconds; "
                    "count-only data does not support percentiles."
                    if defn.family is SignalFamily.LATENCY and not sc.histogram
                    else "Not present in the synthetic scenario."
                )
            caps.append(
                MetricCapability(
                    family=defn.family,
                    signal=defn.signal,
                    status=CapabilityStatus.SUPPORTED
                    if supported
                    else CapabilityStatus.UNSUPPORTED,
                    verified=True,
                    required_metrics=list(defn.required_metrics),
                    required_labels=["project", "env", *defn.identity],
                    observed_metrics=list(defn.required_metrics) if supported else [],
                    reason=reason,
                    history_days=float(sc.history_days) if supported else None,
                )
            )
        return caps

    async def collect(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> CollectionResult:
        cancel.raise_if_cancelled()
        await progress.update(
            StageProgress(stage=StageName.COLLECTION, status=StageStatus.RUNNING, done=0, total=1)
        )
        series, mappings = build_series(self.scenario, scope, windows)
        cancel.raise_if_cancelled()
        await progress.update(
            StageProgress(stage=StageName.COLLECTION, status=StageStatus.DONE, done=1, total=1)
        )
        return CollectionResult(series=series, exclusions=[], mappings=mappings)
