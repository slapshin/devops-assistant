"""Deterministic synthetic MetricsSource for tests, UI development, and offline demos.

Series use neutral, made-up labels (node "shop-production", HTTP job "checkout-api") and
carry whatever matchers the project's scope has, so any project can point at it. Values are
synthetic. The source reports ``backend="synthetic"`` so reports can never be mistaken for live
data.
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
    EntityMapping,
    MappingKind,
    ProgressReporter,
)
from app.domain.jobs import StageName, StageProgress, StageStatus
from app.domain.metrics import CapabilityStatus, MetricCapability, MetricSeries
from app.domain.report import AnalysisWindows, SourceInfo
from app.sources.base import HISTORY_DAYS
from app.sources.prometheus.catalog import BY_SIGNAL, CATALOG
from app.sources.prometheus.source import entity_for, scope_labels

DAY = 288
"""Steps per day."""
N = HISTORY_DAYS * DAY
"""Steps in the collection grid."""
LATEST = N - DAY
"""First step of the latest day."""

INCIDENT_HOST = "shop-production"
SECOND_HOST = "shop-production-2"
NEW_HOST = "shop-production-4"
SERVICE_JOB = "checkout-api"

TASK_ROUTE = {
    "job": SERVICE_JOB,
    "http_route": "/api/v1/orders/:order",
    "http_request_method": "GET",
}
PROXY_JOB = "traefik"
PROXY_SERVICE = {"job": PROXY_JOB, "service": "checkout-api@swarm"}
PROXY_SERVER = {**PROXY_SERVICE, "url": "http://10.0.1.7:8080"}
PG_SERVER = {"job": "postgres", "instance": "pg-primary:9187"}
PG_DATABASE = {**PG_SERVER, "datname": "checkout"}
MYSQL_PRIMARY = {"job": "mysql", "instance": "mysql-primary:9104"}
MYSQL_REPLICA = {"job": "mysql", "instance": "mysql-replica:9104"}
REDIS_MASTER = {"job": "redis", "instance": "redis://redis-master:6379"}
REDIS_REPLICA = {"job": "redis", "instance": "redis://redis-replica:6379"}
QUIET_ROUTE = {
    "job": SERVICE_JOB,
    "http_route": "/internal/order-sla",
    "http_request_method": "GET",
}


@dataclass(frozen=True)
class Scenario:
    """Injected behaviour; anomalies apply to the latest day unless stated otherwise."""

    history_days: int = HISTORY_DAYS
    cpu_load: bool = False
    """shop-production CPU at 95 % for 3 h, ending 2 h before T."""
    memory_growth: bool = False
    disk_depletion: bool = False
    not_found_burst: bool = False
    server_error_burst: bool = False
    latency_shift: bool = False
    traffic_drop: bool = False
    gaps: bool = False
    """A 2 h scrape gap on shop-production during the latest day."""
    flat_memory: bool = False
    """shop-production-2 memory constant (zero MAD)."""
    new_host_days: int | None = None
    """shop-production-4 appears this many days before T (changing population)."""
    histogram: bool = True
    containers: bool = True
    proxies: bool = True
    """Traefik in front of checkout-api."""
    proxy_outage: bool = False
    """One Traefik backend server down for 45 min with a 5xx burst, ending 4 h before T."""
    databases: bool = True
    """A PostgreSQL primary (postgres_exporter) with the checkout database."""
    database_pileup: bool = False
    """An idle-in-transaction session holds locks for 1 h, ending 150 min before T: the oldest
    transaction grows, connections pile up, and blocked writers deadlock and roll back."""
    mysql: bool = True
    """A MySQL primary and replica (mysqld_exporter)."""
    mysql_contention: bool = False
    """Row lock contention on the MySQL primary for 1 h, ending 5 h before T: lock waits and
    slow queries climb, connections reach max_connections and new ones are refused."""
    redis: bool = True
    """A Redis master and replica (redis_exporter, multi-target)."""
    redis_eviction_storm: bool = False
    """The Redis master fills maxmemory for 1 h, ending 7 h before T: keys are evicted, the
    keyspace miss share climbs and commands slow down."""
    recurring_404_days: tuple[int, ...] = ()
    """Trend buckets (1..13) with an extra 404 burst, for recurrence."""
    seed: int = 7
    extra: dict[str, float] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "healthy": Scenario(),
    "incident": Scenario(
        cpu_load=True,
        not_found_burst=True,
        server_error_burst=True,
        recurring_404_days=(3, 5, 9),
        proxy_outage=True,
        database_pileup=True,
        mysql_contention=True,
        redis_eviction_storm=True,
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
        self.scenario = scenario
        self.rng = np.random.default_rng(scenario.seed)
        tod = (np.arange(N) % DAY) / DAY
        self.diurnal = np.sin(2 * np.pi * (tod - 0.35))

    def base(self, level: float, amp: float, noise: float) -> np.ndarray:
        return level + amp * self.diurnal + self.rng.normal(0, noise, N)

    def available(self, values: np.ndarray, start_day: int | None = None) -> np.ndarray:
        out = values.astype(np.float64)
        first = N - self.scenario.history_days * DAY
        if start_day is not None:
            first = max(first, N - start_day * DAY)
        out[: max(0, first)] = np.nan
        return out


class _SeriesBuilder:
    def __init__(self, scope: Scope, windows: AnalysisWindows) -> None:
        self.scope = scope
        self.start = windows.end_time - timedelta(days=HISTORY_DAYS)
        self.series: list[MetricSeries] = []

    def add(self, signal: str, labels: dict[str, str], values: np.ndarray) -> None:
        defn = BY_SIGNAL[signal]
        entity = entity_for(defn, labels)
        query = defn.query.render(self.scope)
        vals = [None if np.isnan(v) else round(float(v), 6) for v in values]
        self.series.append(
            MetricSeries(
                series_id=series_id(query, entity.key),
                family=defn.family,
                signal=signal,
                entity=entity,
                labels={**scope_labels(self.scope), **entity.labels},
                unit=defn.unit,
                query=query,
                step_seconds=STEP_SECONDS,
                start=self.start,
                values=vals,
                coverage=sum(v is not None for v in vals) / N,
            )
        )


def build_series(
    scenario: Scenario, scope: Scope, windows: AnalysisWindows
) -> tuple[list[MetricSeries], list[EntityMapping]]:
    # Generation order matters: every series draws from one seeded RNG.
    gen = _Gen(scenario)
    builder = _SeriesBuilder(scope, windows)

    hosts = [INCIDENT_HOST, SECOND_HOST]
    if scenario.new_host_days is not None:
        hosts.append(NEW_HOST)
    for host in hosts:
        _add_node_series(builder, gen, host)
    for labels, rps in [(TASK_ROUTE, 20.0), (QUIET_ROUTE, 0.05)]:
        _add_route_series(builder, gen, labels, rps)
    if scenario.containers:
        for host in hosts[:2]:
            builder.add(
                "container_cpu",
                {"instance": host, "container": "shop_checkout-api.1"},
                gen.available(np.clip(gen.base(0.3, 0.05, 0.01), 0, None)),
            )

    if scenario.proxies:
        _add_proxy_series(builder, gen)
    if scenario.databases:
        _add_database_series(builder, gen)
    if scenario.mysql:
        _add_mysql_series(builder, gen)
    if scenario.redis:
        _add_redis_series(builder, gen)

    mappings = [
        EntityMapping(
            kind=MappingKind.OTEL_SERVICE,
            service=SERVICE_JOB,
            host=SECOND_HOST,
            observed_at=windows.end_time,
        ),
    ]
    return builder.series, mappings


def _add_node_series(builder: _SeriesBuilder, gen: _Gen, host: str) -> None:
    scenario = gen.scenario
    node = {"job": "node", "instance": host}
    start_day = scenario.new_host_days if host == NEW_HOST else None

    cpu = np.clip(gen.base(0.22, 0.05, 0.01), 0, 1)
    if scenario.cpu_load and host == INCIDENT_HOST:
        cpu[N - 60 : N - 24] = 0.95 + gen.rng.normal(0, 0.005, 36)
    if scenario.gaps and host == INCIDENT_HOST:
        cpu[LATEST + 100 : LATEST + 124] = np.nan
    builder.add("cpu_utilization", node, gen.available(cpu, start_day))

    mem = gen.base(0.40, 0.01, 0.005)
    if scenario.flat_memory and host == SECOND_HOST:
        mem = np.full(N, 0.35)
    if scenario.memory_growth and host == INCIDENT_HOST:
        mem[LATEST:] = np.linspace(0.40, 0.93, DAY)
    builder.add("memory_utilization", node, gen.available(mem, start_day))

    fs = 0.50 + np.linspace(0, 0.01, N) + gen.rng.normal(0, 0.0005, N)
    if scenario.disk_depletion and host == SECOND_HOST:
        fs[LATEST:] = np.linspace(0.51, 0.97, DAY)
    builder.add(
        "filesystem_used_ratio",
        {"job": "node", "instance": host, "device": "/dev/sda1", "mountpoint": "/"},
        gen.available(fs, start_day),
    )


def _add_route_series(
    builder: _SeriesBuilder, gen: _Gen, labels: dict[str, str], rps: float
) -> None:
    scenario = gen.scenario
    is_task_route = labels is TASK_ROUTE

    req = np.clip(gen.base(rps, rps * 0.3, rps * 0.03), 0, None)
    if scenario.traffic_drop and is_task_route:
        req[N - 48 :] = req[N - 48 :] * 0.1

    nf = np.clip(req * 0.04 + gen.rng.normal(0, 0.02 * rps, N), 0, None)
    if is_task_route:
        if scenario.not_found_burst:
            nf[N - 108 : N - 90] = req[N - 108 : N - 90] * 0.3
        for b in scenario.recurring_404_days:
            s = N - (b + 1) * DAY + 150
            nf[s : s + 9] = req[s : s + 9] * 0.2
    builder.add("http_requests", labels, gen.available(req))
    builder.add("http_404", labels, gen.available(nf))
    builder.add("http_4xx", labels, gen.available(nf + 0.01 * req))

    if scenario.server_error_burst and is_task_route:
        err = np.full(N, np.nan)
        err[N - 30 : N - 18] = req[N - 30 : N - 18] * 0.12
        builder.add("http_5xx", labels, err)

    if scenario.histogram:
        p95 = np.clip(gen.base(0.08, 0.01, 0.004), 0.001, None)
        if scenario.latency_shift and is_task_route:
            p95[N - 72 :] = 0.4 + gen.rng.normal(0, 0.01, 72)
        builder.add("http_latency_p95", labels, gen.available(p95))
        builder.add("http_latency_p99", labels, gen.available(p95 * 1.6))
    else:
        mean = np.clip(gen.base(0.03, 0.005, 0.002), 0.001, None)
        builder.add("http_latency_mean", labels, gen.available(mean))


def _add_proxy_series(builder: _SeriesBuilder, gen: _Gen) -> None:
    """Traefik service traffic (mirroring the task route) and its backend server state."""
    scenario = gen.scenario
    outage = slice(N - 57, N - 48)

    req = np.clip(gen.base(25.0, 7.5, 0.75), 0, None)
    server_errors = np.clip(req * 0.001 + gen.rng.normal(0, 0.005, N), 0, None)
    down = np.zeros(N)
    if scenario.proxy_outage:
        server_errors[outage] = req[outage] * 0.25
        down[outage] = 1.0
    builder.add("traefik_requests", PROXY_SERVICE, gen.available(req))
    builder.add("traefik_5xx", PROXY_SERVICE, gen.available(server_errors))
    not_found = np.clip(req * 0.02 + gen.rng.normal(0, 0.05, N), 0, None)
    builder.add("traefik_404", PROXY_SERVICE, gen.available(not_found))
    builder.add("traefik_4xx", PROXY_SERVICE, gen.available(not_found + 0.005 * req))
    builder.add("traefik_server_down", PROXY_SERVER, gen.available(down))
    if scenario.histogram:
        p95 = np.clip(gen.base(0.09, 0.01, 0.004), 0.001, None)
        builder.add("traefik_latency_p95", PROXY_SERVICE, gen.available(p95))
        builder.add("traefik_latency_p99", PROXY_SERVICE, gen.available(p95 * 1.5))
    builder.add(
        "traefik_connections_active",
        {"job": PROXY_JOB, "entrypoint": "websecure"},
        gen.available(np.clip(gen.base(120.0, 30.0, 4.0), 0, None)),
    )


def _add_database_series(builder: _SeriesBuilder, gen: _Gen) -> None:
    scenario = gen.scenario
    pileup = slice(N - 42, N - 30)
    steps = pileup.stop - pileup.start

    connections = np.clip(gen.base(0.25, 0.05, 0.01), 0, 1)
    transactions = np.clip(gen.base(150.0, 45.0, 4.5), 0, None)
    rollbacks = np.clip(transactions * 0.005 + gen.rng.normal(0, 0.05, N), 0, None)
    deadlocks = np.zeros(N)
    longest = np.clip(gen.base(2.0, 0.5, 0.5), 0, None)
    if scenario.database_pileup:
        connections[pileup] = np.linspace(0.3, 0.9, steps)
        rollbacks[pileup] = transactions[pileup] * 0.2
        deadlocks[N - 36 : N - 33] = 2.0
        longest[pileup] = np.arange(1, steps + 1) * STEP_SECONDS
    builder.add("pg_down", PG_SERVER, gen.available(np.zeros(N)))
    builder.add("pg_connections_used_ratio", PG_SERVER, gen.available(connections))
    builder.add("pg_replication_lag", PG_SERVER, gen.available(np.zeros(N)))
    builder.add("pg_transactions", PG_DATABASE, gen.available(transactions))
    builder.add("pg_rollbacks", PG_DATABASE, gen.available(rollbacks))
    builder.add("pg_deadlocks", PG_DATABASE, gen.available(deadlocks))
    temp_bytes = np.clip(gen.base(200 * 1024.0, 50 * 1024.0, 10 * 1024.0), 0, None)
    builder.add("pg_temp_bytes", PG_DATABASE, gen.available(temp_bytes))
    builder.add("pg_longest_transaction", PG_DATABASE, gen.available(longest))


def _add_mysql_series(builder: _SeriesBuilder, gen: _Gen) -> None:
    contention = slice(N - 72, N - 60)
    steps = contention.stop - contention.start
    for server in (MYSQL_PRIMARY, MYSQL_REPLICA):
        connections = np.clip(gen.base(0.3, 0.08, 0.01), 0, 1)
        queries = np.clip(gen.base(400.0, 120.0, 12.0), 0, None)
        slow = np.clip(queries * 0.001 + gen.rng.normal(0, 0.02, N), 0, None)
        lock_waits = np.clip(gen.base(0.5, 0.2, 0.1), 0, None)
        refused = np.zeros(N)
        if gen.scenario.mysql_contention and server is MYSQL_PRIMARY:
            connections[contention] = np.linspace(0.4, 1.0, steps)
            slow[contention] = queries[contention] * 0.05
            lock_waits[contention] = 25.0
            refused[N - 64 : N - 61] = 40.0
        tmp_disk = np.clip(gen.base(2.0, 0.5, 0.2), 0, None)
        builder.add("mysql_down", server, gen.available(np.zeros(N)))
        builder.add("mysql_connections_used_ratio", server, gen.available(connections))
        builder.add("mysql_connections_refused", server, gen.available(refused))
        builder.add("mysql_queries", server, gen.available(queries))
        builder.add("mysql_slow_queries", server, gen.available(slow))
        builder.add("mysql_row_lock_waits", server, gen.available(lock_waits))
        builder.add("mysql_tmp_disk_tables", server, gen.available(tmp_disk))
    lag = np.clip(gen.base(0.5, 0.2, 0.3), 0, None)
    builder.add("mysql_slave_lag", MYSQL_REPLICA, gen.available(lag))
    builder.add("mysql_slave_stopped", MYSQL_REPLICA, gen.available(np.zeros(N)))


def _add_redis_series(builder: _SeriesBuilder, gen: _Gen) -> None:
    storm = slice(N - 96, N - 84)
    steps = storm.stop - storm.start
    for server in (REDIS_MASTER, REDIS_REPLICA):
        clients = np.clip(gen.base(0.05, 0.01, 0.003), 0, 1)
        memory = np.clip(gen.base(0.6, 0.02, 0.005), 0, 1)
        evictions = np.clip(gen.base(0.2, 0.1, 0.05), 0, None)
        commands = np.clip(gen.base(2000.0, 600.0, 60.0), 0, None)
        latency = np.clip(gen.base(5e-6, 1e-6, 3e-7), 1e-6, None)
        lookups = commands * 0.6
        misses = np.clip(lookups * 0.05 + gen.rng.normal(0, 2.0, N), 0, None)
        if gen.scenario.redis_eviction_storm and server is REDIS_MASTER:
            memory[storm] = np.linspace(0.8, 1.0, steps)
            evictions[storm] = 200.0
            misses[storm] = lookups[storm] * 0.4
            latency[storm] = 0.002
        builder.add("redis_down", server, gen.available(np.zeros(N)))
        builder.add("redis_clients_used_ratio", server, gen.available(clients))
        builder.add("redis_rejected_connections", server, gen.available(np.zeros(N)))
        builder.add("redis_memory_used_ratio", server, gen.available(memory))
        builder.add("redis_evictions", server, gen.available(evictions))
        builder.add("redis_persistence_failed", server, gen.available(np.zeros(N)))
        builder.add("redis_commands", server, gen.available(commands))
        builder.add("redis_command_latency_mean", server, gen.available(latency))
        builder.add("redis_keyspace_lookups", server, gen.available(lookups))
        builder.add("redis_keyspace_misses", server, gen.available(misses))
    builder.add("redis_replica_link_down", REDIS_REPLICA, gen.available(np.zeros(N)))


class SyntheticMetricsSource:
    def __init__(self, scenario: Scenario | str = "incident") -> None:
        self.scenario = SCENARIOS[scenario] if isinstance(scenario, str) else scenario

    def with_scenario(self, **changes: object) -> SyntheticMetricsSource:
        return SyntheticMetricsSource(replace(self.scenario, **changes))  # type: ignore[arg-type]

    async def source_info(self) -> SourceInfo:
        return SourceInfo(base_url="synthetic://fixtures", backend="synthetic", version=None)

    async def capabilities(self, scope: Scope, windows: AnalysisWindows) -> list[MetricCapability]:
        scenario = self.scenario
        produced = {
            "cpu_utilization",
            "memory_utilization",
            "filesystem_used_ratio",
            "http_requests",
            "http_404",
            "http_4xx",
            "http_5xx",
            *(
                ("http_latency_p95", "http_latency_p99")
                if scenario.histogram
                else ("http_latency_mean",)
            ),
            *(("container_cpu",) if scenario.containers else ()),
            *(
                (
                    "traefik_requests",
                    "traefik_5xx",
                    "traefik_404",
                    "traefik_4xx",
                    "traefik_server_down",
                    "traefik_connections_active",
                    *(("traefik_latency_p95", "traefik_latency_p99") if scenario.histogram else ()),
                )
                if scenario.proxies
                else ()
            ),
            *(
                (d.signal for d in CATALOG if d.signal.startswith("pg_"))
                if scenario.databases
                else ()
            ),
            *(
                (
                    d.signal
                    for d in CATALOG
                    if d.signal.startswith("mysql_") and not d.signal.startswith("mysql_replica_")
                )
                if scenario.mysql
                else ()
            ),
            *(
                (d.signal for d in CATALOG if d.signal.startswith("redis_"))
                if scenario.redis
                else ()
            ),
        }
        caps = []
        for defn in CATALOG:
            supported = defn.signal in produced
            reason = None
            if not supported:
                reason = (
                    f"No histogram buckets for "
                    f"{defn.required_metrics[0].removesuffix('_bucket')}; "
                    "count-only data does not support percentiles."
                    if defn.family is SignalFamily.LATENCY and not scenario.histogram
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
                    required_labels=[*scope.label_names, *defn.identity],
                    observed_metrics=list(defn.required_metrics) if supported else [],
                    reason=reason,
                    history_days=float(scenario.history_days) if supported else None,
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
