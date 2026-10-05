"""Turn collected series into analysable series (ratios, event deltas, volume guards)."""

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.analysis.rules import RULES, Rule
from app.domain.common import Entity, Unit
from app.domain.metrics import MetricSeries

Array = NDArray[np.float64]

DIRECT: dict[str, str] = {
    "cpu_utilization": "cpu_utilization",
    "cpu_iowait": "cpu_iowait",
    "memory_utilization": "memory_utilization",
    "memory_pressure": "memory_pressure",
    "swap_used_ratio": "swap_used_ratio",
    "node_oom_kills": "node_oom_kills",
    "io_pressure": "io_pressure",
    "disk_busy_ratio": "disk_busy_ratio",
    "disk_io_bytes": "disk_io_bytes",
    "filesystem_used_ratio": "filesystem_used_ratio",
    "filesystem_inodes_used_ratio": "filesystem_inodes_used_ratio",
    "network_receive_bytes": "network_receive_bytes",
    "network_transmit_bytes": "network_transmit_bytes",
    "network_errors": "network_errors",
    "container_cpu": "container_cpu",
    "container_memory_working_set": "container_memory_working_set",
    "container_memory_limit_ratio": "container_memory_limit_ratio",
    "container_throttling_ratio": "container_throttling_ratio",
    "container_oom": "container_oom",
    "swarm_replica_shortfall": "swarm_replica_shortfall",
    "nginx_connections_active": "proxy_connections_active",
    "angie_connections_active": "proxy_connections_active",
    "traefik_connections_active": "proxy_connections_active",
    "nginx_connections_dropped": "proxy_connections_dropped",
    "angie_connections_dropped": "proxy_connections_dropped",
    "nginx_down": "proxy_down",
    "angie_peer_unavailable": "upstream_unavailable",
    "caddy_upstream_unhealthy": "upstream_unavailable",
    "traefik_server_down": "upstream_unavailable",
    "pg_down": "database_down",
    "pg_connections_used_ratio": "database_connections_ratio",
    "pg_replication_lag": "database_replication_lag",
    "pg_deadlocks": "database_deadlocks",
    "pg_temp_bytes": "database_temp_bytes",
    "pg_longest_transaction": "database_longest_transaction",
    "mysql_down": "database_down",
    "mysql_connections_used_ratio": "database_connections_ratio",
    "mysql_connections_refused": "database_connections_refused",
    "mysql_slave_lag": "database_replication_lag",
    "mysql_replica_lag": "database_replication_lag",
    "mysql_slave_stopped": "database_replication_stopped",
    "mysql_replica_stopped": "database_replication_stopped",
    "mysql_row_lock_waits": "database_lock_waits",
    "mysql_tmp_disk_tables": "database_tmp_disk_tables",
    "redis_down": "database_down",
    "redis_clients_used_ratio": "database_connections_ratio",
    "redis_rejected_connections": "database_connections_refused",
    "redis_memory_used_ratio": "database_memory_ratio",
    "redis_evictions": "database_evictions",
    "redis_replica_link_down": "database_replica_link_down",
    "redis_persistence_failed": "database_persistence_failed",
}
"""Collected signal -> rule for signals analysed as collected."""

SWARM_FAILED_TASKS = "swarm_failed_tasks"
MEAN_LATENCY_RULE = "latency_mean"
STATUS_CODE_ATTRIBUTE = "http_response_status_code"


@dataclass(frozen=True)
class _TrafficSpec:
    """How one request-count signal fans out into analysable rate, ratio and latency series."""

    traffic_signal: str
    rate_rule: str | None
    """None when the count only guards ratios (Redis keyspace lookups)."""
    error_ratios: tuple[tuple[str, str], ...] = ()
    """(numerator signal, ratio rule) pairs divided by the request rate."""
    quantile_signal: str | None = None
    quantile_rule: str = "latency_p95"
    mean_signal: str | None = None
    """Latency fallback when no histogram quantile is collected."""
    tail_signal: str | None = None
    """Higher quantile attached as extra evidence."""
    client_errors: tuple[str, str] | None = None
    """(404 signal, 4xx signal) from which 404 and other 4xx rates are derived."""


_TRAFFIC_SPECS = (
    _TrafficSpec(
        traffic_signal="http_requests",
        rate_rule="request_rate",
        error_ratios=(("http_5xx", "server_error_ratio"),),
        quantile_signal="http_latency_p95",
        quantile_rule="latency_p95",
        mean_signal="http_latency_mean",
        tail_signal="http_latency_p99",
        client_errors=("http_404", "http_4xx"),
    ),
    _TrafficSpec(
        traffic_signal="rpc_requests",
        rate_rule="rpc_request_rate",
        error_ratios=(("rpc_errors", "rpc_error_ratio"),),
        quantile_signal="rpc_latency_p95",
        quantile_rule="rpc_latency_p95",
    ),
    # Reverse proxies reuse the HTTP rules; their entities say which proxy and zone/service.
    _TrafficSpec(traffic_signal="nginx_requests", rate_rule="request_rate"),
    _TrafficSpec(
        traffic_signal="angie_requests",
        rate_rule="request_rate",
        error_ratios=(("angie_5xx", "server_error_ratio"),),
        client_errors=("angie_404", "angie_4xx"),
    ),
    *(
        _TrafficSpec(
            traffic_signal=f"{proxy}_requests",
            rate_rule="request_rate",
            error_ratios=((f"{proxy}_5xx", "server_error_ratio"),),
            quantile_signal=f"{proxy}_latency_p95",
            tail_signal=f"{proxy}_latency_p99",
            client_errors=(f"{proxy}_404", f"{proxy}_4xx"),
        )
        for proxy in ("caddy", "traefik")
    ),
    # Rolled-back share of all transactions, volume-guarded like the 5xx ratio.
    _TrafficSpec(
        traffic_signal="pg_transactions",
        rate_rule="database_transaction_rate",
        error_ratios=(("pg_rollbacks", "database_rollback_ratio"),),
    ),
    # Share of statements slower than long_query_time.
    _TrafficSpec(
        traffic_signal="mysql_queries",
        rate_rule="database_query_rate",
        error_ratios=(("mysql_slow_queries", "database_slow_query_ratio"),),
    ),
    # Redis command rate with the commandstats mean latency, guarded by the command volume.
    _TrafficSpec(
        traffic_signal="redis_commands",
        rate_rule="database_command_rate",
        quantile_signal="redis_command_latency_mean",
        quantile_rule="database_command_latency",
    ),
    # Share of key lookups that missed; the lookup count is not analysed on its own.
    _TrafficSpec(
        traffic_signal="redis_keyspace_lookups",
        rate_rule=None,
        error_ratios=(("redis_keyspace_misses", "database_cache_miss_ratio"),),
    ),
)


@dataclass
class AnalysisSeries:
    rule: Rule
    entity: Entity
    unit: Unit
    values: Array
    query: str
    source: MetricSeries
    volume: Array | None = None
    """Requests per step for volume-guarded rules."""
    extra_evidence: list[MetricSeries] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule.name, self.entity.key)


def to_array(series: MetricSeries) -> Array:
    return np.array([np.nan if v is None else v for v in series.values], dtype=np.float64)


def _zero_where_observed(numerator: Array | None, denominator: Array) -> Array:
    """Counters for a status appear only once it occurs: absent numerator is 0 where the
    denominator was observed, and unknown where it was not."""
    num = np.zeros_like(denominator) if numerator is None else numerator.copy()
    num[np.isnan(num) & ~np.isnan(denominator)] = 0.0
    num[np.isnan(denominator)] = np.nan
    return num


def derive(
    series: list[MetricSeries], step_seconds: int, min_requests: int
) -> list[AnalysisSeries]:
    by_signal: dict[str, dict[str, MetricSeries]] = defaultdict(dict)
    for s in series:
        by_signal[s.signal][s.entity.key] = s

    out = _direct_series(by_signal)
    out += _task_failure_series(by_signal)
    for spec in _TRAFFIC_SPECS:
        out += _traffic_series(by_signal, spec, step_seconds, min_requests)
    return out


def _direct_series(by_signal: dict[str, dict[str, MetricSeries]]) -> list[AnalysisSeries]:
    out: list[AnalysisSeries] = []
    for name in sorted(DIRECT.keys() & by_signal.keys()):
        for key in sorted(by_signal[name]):
            s = by_signal[name][key]
            rule = RULES[DIRECT[name]]
            out.append(AnalysisSeries(rule, s.entity, s.unit, to_array(s), s.query, s))
    return out


def _task_failure_series(by_signal: dict[str, dict[str, MetricSeries]]) -> list[AnalysisSeries]:
    """Failed-task counts are cumulative gauges; analyse the per-step increase instead."""
    failed_tasks = by_signal.get(SWARM_FAILED_TASKS, {})
    out: list[AnalysisSeries] = []
    for key in sorted(failed_tasks):
        s = failed_tasks[key]
        delta = np.diff(to_array(s), prepend=np.nan)
        delta[delta < 0] = 0.0  # pruned task history: decreases carry no information
        out.append(
            AnalysisSeries(
                RULES["swarm_task_failures"],
                s.entity,
                Unit.COUNT,
                delta,
                f"delta of: {s.query}",
                s,
            )
        )
    return out


def _traffic_series(
    by_signal: dict[str, dict[str, MetricSeries]],
    spec: _TrafficSpec,
    step_seconds: int,
    min_requests: int,
) -> list[AnalysisSeries]:
    """Request rate plus the volume-guarded ratios, 4xx rates and latency of each route."""
    out: list[AnalysisSeries] = []
    for key in sorted(by_signal.get(spec.traffic_signal, {})):
        requests = by_signal[spec.traffic_signal][key]
        rate = to_array(requests)
        volume = rate * step_seconds
        if spec.rate_rule is not None:
            out.append(
                AnalysisSeries(
                    RULES[spec.rate_rule],
                    requests.entity,
                    requests.unit,
                    rate,
                    requests.query,
                    requests,
                )
            )

        guarded_rate = rate.copy()
        guarded_rate[volume < min_requests] = np.nan
        for numerator_signal, rule_name in spec.error_ratios:
            numerator_series = by_signal.get(numerator_signal, {}).get(key)
            out.append(
                _error_ratio(
                    numerator_series, requests, rate, guarded_rate, volume, RULES[rule_name]
                )
            )

        if spec.client_errors is not None:
            out += _client_error_series(by_signal, spec.client_errors, key, requests, rate)

        latency = _latency_series(by_signal, spec, key, requests, volume, min_requests)
        if latency is not None:
            out.append(latency)
    return out


def _error_ratio(
    numerator_series: MetricSeries | None,
    requests: MetricSeries,
    rate: Array,
    guarded_rate: Array,
    volume: Array,
    rule: Rule,
) -> AnalysisSeries:
    numerator = _zero_where_observed(to_array(numerator_series) if numerator_series else None, rate)
    ratio = np.full_like(rate, np.nan)
    has_volume = ~np.isnan(guarded_rate)
    ratio[has_volume] = numerator[has_volume] / guarded_rate[has_volume]

    source = numerator_series or requests
    return AnalysisSeries(
        rule,
        requests.entity,
        Unit.RATIO,
        ratio,
        f"({source.query})\n/\n({requests.query})",
        source,
        volume=volume,
        extra_evidence=[requests],
    )


def _client_error_series(
    by_signal: dict[str, dict[str, MetricSeries]],
    signals: tuple[str, str],
    key: str,
    requests: MetricSeries,
    rate: Array,
) -> list[AnalysisSeries]:
    """404s on their own, and other 4xx with the 404s subtracted so neither is counted twice."""
    not_found_signal, client_error_signal = signals
    not_found_series = by_signal.get(not_found_signal, {}).get(key)
    client_error_series = by_signal.get(client_error_signal, {}).get(key)
    not_found = _zero_where_observed(
        to_array(not_found_series) if not_found_series is not None else None, rate
    )
    client_errors = _zero_where_observed(
        to_array(client_error_series) if client_error_series is not None else None, rate
    )

    not_found_source = not_found_series or requests
    client_error_source = client_error_series or requests
    return [
        AnalysisSeries(
            RULES["not_found_rate"],
            requests.entity,
            Unit.REQUESTS_PER_SECOND,
            not_found,
            not_found_source.query,
            not_found_source,
            extra_evidence=[requests],
            attributes={STATUS_CODE_ATTRIBUTE: "404"},
        ),
        AnalysisSeries(
            RULES["client_error_rate"],
            requests.entity,
            Unit.REQUESTS_PER_SECOND,
            np.clip(client_errors - not_found, 0.0, None),
            f"({client_error_source.query})\n-\n({not_found_source.query})",
            client_error_source,
            extra_evidence=[requests],
            attributes={STATUS_CODE_ATTRIBUTE: "4xx excl. 404"},
        ),
    ]


def _latency_series(
    by_signal: dict[str, dict[str, MetricSeries]],
    spec: _TrafficSpec,
    key: str,
    requests: MetricSeries,
    volume: Array,
    min_requests: int,
) -> AnalysisSeries | None:
    """Prefer the histogram quantile; fall back to the mean where no histogram exists."""
    latency = by_signal.get(spec.quantile_signal, {}).get(key) if spec.quantile_signal else None
    rule_name = spec.quantile_rule
    if latency is None and spec.mean_signal is not None:
        latency = by_signal.get(spec.mean_signal, {}).get(key)
        rule_name = MEAN_LATENCY_RULE
    if latency is None:
        return None

    values = to_array(latency)
    values[volume < min_requests] = np.nan
    extra_evidence = [requests]
    if spec.tail_signal is not None:
        tail = by_signal.get(spec.tail_signal, {}).get(key)
        if tail is not None:
            extra_evidence.insert(0, tail)

    return AnalysisSeries(
        RULES[rule_name],
        requests.entity,
        Unit.SECONDS,
        values,
        latency.query,
        latency,
        volume=volume,
        extra_evidence=extra_evidence,
    )
