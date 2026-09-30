"""Turn collected series into analysable series (ratios, event deltas, volume guards)."""

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.analysis.rules import RULES, Rule
from app.domain.common import Entity, Unit
from app.domain.metrics import MetricSeries

Array = NDArray[np.float64]

DIRECT = {
    "cpu_utilization",
    "cpu_iowait",
    "memory_utilization",
    "memory_pressure",
    "swap_used_ratio",
    "node_oom_kills",
    "io_pressure",
    "disk_busy_ratio",
    "disk_io_bytes",
    "filesystem_used_ratio",
    "filesystem_inodes_used_ratio",
    "network_receive_bytes",
    "network_transmit_bytes",
    "network_errors",
    "container_cpu",
    "container_memory_working_set",
    "container_memory_limit_ratio",
    "container_throttling_ratio",
    "container_oom",
    "swarm_replica_shortfall",
}


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
    by: dict[str, dict[str, MetricSeries]] = defaultdict(dict)
    for s in series:
        by[s.signal][s.entity.key] = s
    out: list[AnalysisSeries] = []

    for name in sorted(DIRECT & set(by)):
        for key in sorted(by[name]):
            s = by[name][key]
            out.append(AnalysisSeries(RULES[name], s.entity, s.unit, to_array(s), s.query, s))

    for key in sorted(by.get("swarm_failed_tasks", {})):
        s = by["swarm_failed_tasks"][key]
        values = to_array(s)
        delta = np.diff(values, prepend=np.nan)
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

    for traffic, rule_rate, pairs, latency in (
        (
            "http_requests",
            "request_rate",
            (("http_5xx", "server_error_ratio"),),
            ("http_latency_p95", "http_latency_mean"),
        ),
        (
            "rpc_requests",
            "rpc_request_rate",
            (("rpc_errors", "rpc_error_ratio"),),
            ("rpc_latency_p95", None),
        ),
    ):
        for key in sorted(by.get(traffic, {})):
            req = by[traffic][key]
            rate = to_array(req)
            volume = rate * step_seconds
            out.append(AnalysisSeries(RULES[rule_rate], req.entity, req.unit, rate, req.query, req))
            guarded = rate.copy()
            guarded[volume < min_requests] = np.nan
            for num_signal, rule_name in pairs:
                num_series = by.get(num_signal, {}).get(key)
                num = _zero_where_observed(to_array(num_series) if num_series else None, rate)
                ratio = np.full_like(rate, np.nan)
                ok = ~np.isnan(guarded)
                ratio[ok] = num[ok] / guarded[ok]
                query = f"({(num_series or req).query})\n/\n({req.query})"
                out.append(
                    AnalysisSeries(
                        RULES[rule_name],
                        req.entity,
                        Unit.RATIO,
                        ratio,
                        query,
                        num_series or req,
                        volume=volume,
                        extra_evidence=[req],
                    )
                )
            if traffic == "http_requests":
                n404 = _zero_where_observed(
                    to_array(by["http_404"][key]) if key in by.get("http_404", {}) else None, rate
                )
                n4xx = _zero_where_observed(
                    to_array(by["http_4xx"][key]) if key in by.get("http_4xx", {}) else None, rate
                )
                src404 = by.get("http_404", {}).get(key, req)
                src4xx = by.get("http_4xx", {}).get(key, req)
                out.append(
                    AnalysisSeries(
                        RULES["not_found_rate"],
                        req.entity,
                        Unit.REQUESTS_PER_SECOND,
                        n404,
                        src404.query,
                        src404,
                        extra_evidence=[req],
                        attributes={"http_response_status_code": "404"},
                    )
                )
                out.append(
                    AnalysisSeries(
                        RULES["client_error_rate"],
                        req.entity,
                        Unit.REQUESTS_PER_SECOND,
                        np.clip(n4xx - n404, 0.0, None),
                        f"({src4xx.query})\n-\n({src404.query})",
                        src4xx,
                        extra_evidence=[req],
                        attributes={"http_response_status_code": "4xx excl. 404"},
                    )
                )
            quantile, mean = latency
            lat = by.get(quantile, {}).get(key) or (by.get(mean, {}).get(key) if mean else None)
            if lat is not None:
                values = to_array(lat)
                values[volume < min_requests] = np.nan
                rule = (
                    "latency_mean"
                    if lat.signal == "http_latency_mean"
                    else ("latency_p95" if traffic == "http_requests" else "rpc_latency_p95")
                )
                extra = [req]
                if (p99 := by.get("http_latency_p99", {}).get(key)) is not None:
                    extra.insert(0, p99)
                out.append(
                    AnalysisSeries(
                        RULES[rule],
                        req.entity,
                        Unit.SECONDS,
                        values,
                        lat.query,
                        lat,
                        volume=volume,
                        extra_evidence=extra,
                    )
                )
    return out
