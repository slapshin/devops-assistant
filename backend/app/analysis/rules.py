"""Analysis rules: which collected signals are analysed, how, and under which thresholds."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.common import Severity, SignalFamily, Unit


class RuleKind(StrEnum):
    LEVEL = "level"
    """Relative robust baseline plus optional absolute heuristic."""
    EVENT = "event"
    """Any positive count in a step is anomalous (OOM kills, new task failures)."""
    SHORTFALL = "shortfall"
    """Value >= 1 sustained for shortfall_min_minutes (missing replicas)."""


class Dir(StrEnum):
    UP = "up"
    DOWN = "down"
    BOTH = "both"


@dataclass(frozen=True)
class Rule:
    name: str
    family: SignalFamily
    label: str
    kind: RuleKind = RuleKind.LEVEL
    thresholds: str | None = None
    direction: Dir = Dir.UP
    volume_guard: bool = False
    volume_noun: str = "requests"
    """What the guarded volume counts, for confidence reasons."""
    severity_cap: Severity | None = None
    event_points: int = 0
    title_up: str | None = None
    title_down: str | None = None

    def title(self, *, up: bool, threshold: float | None, unit: Unit) -> str:
        if threshold is not None and self.kind is RuleKind.LEVEL:
            return f"{self.label} at or above {format_value(threshold, unit)} (heuristic)"
        if self.kind is not RuleKind.LEVEL:
            return self.title_up or self.label
        custom = self.title_up if up else self.title_down
        return custom or f"{self.label} {'above' if up else 'below'} expected range"


def format_value(value: float, unit: Unit) -> str:
    match unit:
        case Unit.RATIO:
            return f"{value * 100:.1f} %"
        case Unit.SECONDS:
            return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"
        case Unit.BYTES:
            return _bytes(value)
        case Unit.BYTES_PER_SECOND:
            return f"{_bytes(value)}/s"
        case Unit.REQUESTS_PER_SECOND:
            return f"{value:.2f} req/s"
        case Unit.PER_SECOND:
            return f"{value:.3g}/s"
        case _:
            return f"{value:.3g}"


def _bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


F = SignalFamily
RULES: dict[str, Rule] = {
    r.name: r
    for r in (
        Rule("cpu_utilization", F.CPU, "CPU utilisation", thresholds="cpu_utilization"),
        Rule("cpu_iowait", F.CPU, "CPU I/O wait", thresholds="cpu_iowait"),
        Rule("memory_utilization", F.MEMORY, "Memory utilisation", thresholds="memory_utilization"),
        Rule("memory_pressure", F.MEMORY, "Memory pressure (PSI)", thresholds="memory_pressure"),
        Rule("swap_used_ratio", F.MEMORY, "Swap usage", thresholds="memory_utilization"),
        Rule(
            "node_oom_kills",
            F.MEMORY,
            "Kernel OOM kills",
            kind=RuleKind.EVENT,
            event_points=3,
            title_up="Kernel OOM kill",
        ),
        Rule("io_pressure", F.DISK_IO, "I/O pressure (PSI)", thresholds="io_pressure"),
        Rule("disk_busy_ratio", F.DISK_IO, "Disk busy time", thresholds="disk_busy_ratio"),
        Rule(
            "disk_io_bytes",
            F.DISK_IO,
            "Disk throughput",
            thresholds="disk_io_bytes",
            direction=Dir.BOTH,
            severity_cap=Severity.LOW,
        ),
        Rule(
            "filesystem_used_ratio",
            F.FILESYSTEM,
            "Filesystem usage",
            thresholds="filesystem_used_ratio",
        ),
        Rule(
            "filesystem_inodes_used_ratio",
            F.FILESYSTEM,
            "Inode usage",
            thresholds="filesystem_inodes_used_ratio",
        ),
        Rule(
            "network_receive_bytes",
            F.NETWORK,
            "Network receive throughput",
            thresholds="network_bytes",
            direction=Dir.BOTH,
            severity_cap=Severity.MEDIUM,
        ),
        Rule(
            "network_transmit_bytes",
            F.NETWORK,
            "Network transmit throughput",
            thresholds="network_bytes",
            direction=Dir.BOTH,
            severity_cap=Severity.MEDIUM,
        ),
        Rule("network_errors", F.NETWORK, "Network errors and drops", thresholds="network_errors"),
        Rule("container_cpu", F.CONTAINER, "Container CPU", thresholds="container_cpu"),
        Rule(
            "container_memory_working_set",
            F.CONTAINER,
            "Container memory",
            thresholds="container_memory_working_set",
        ),
        Rule(
            "container_memory_limit_ratio",
            F.CONTAINER,
            "Container memory vs limit",
            thresholds="container_memory_limit_ratio",
        ),
        Rule(
            "container_throttling_ratio",
            F.CONTAINER,
            "Container CPU throttling",
            thresholds="container_throttling_ratio",
        ),
        Rule(
            "container_oom",
            F.CONTAINER,
            "Container OOM events",
            kind=RuleKind.EVENT,
            event_points=3,
            title_up="Container OOM kill",
        ),
        Rule(
            "swarm_task_failures",
            F.CONTAINER,
            "New failed tasks",
            kind=RuleKind.EVENT,
            event_points=2,
            title_up="New failed or rejected Swarm tasks (restart proxy)",
        ),
        Rule(
            "swarm_replica_shortfall",
            F.CONTAINER,
            "Replica shortfall",
            kind=RuleKind.SHORTFALL,
            event_points=3,
            title_up="Running replicas below desired",
        ),
        Rule(
            "request_rate",
            F.REQUEST_TRAFFIC,
            "Request rate",
            thresholds="request_rate",
            direction=Dir.BOTH,
            title_up="Traffic increase",
            title_down="Traffic drop",
        ),
        Rule(
            "server_error_ratio",
            F.REQUEST_FAILURES,
            "Server error rate (5xx)",
            thresholds="server_error_ratio",
            volume_guard=True,
        ),
        Rule(
            "not_found_rate",
            F.CLIENT_ERRORS,
            "404 responses",
            thresholds="not_found_rate",
            severity_cap=Severity.MEDIUM,
            title_up="404 increase",
        ),
        Rule(
            "client_error_rate",
            F.CLIENT_ERRORS,
            "Client errors (4xx excl. 404)",
            thresholds="client_error_rate",
            severity_cap=Severity.MEDIUM,
            title_up="Client error increase (4xx, excl. 404)",
        ),
        Rule(
            "latency_p95",
            F.LATENCY,
            "p95 latency",
            thresholds="latency_quantile",
            volume_guard=True,
        ),
        Rule(
            "latency_mean",
            F.LATENCY,
            "Mean latency",
            thresholds="latency_quantile",
            volume_guard=True,
        ),
        Rule(
            "rpc_request_rate",
            F.REQUEST_TRAFFIC,
            "RPC call rate",
            thresholds="request_rate",
            direction=Dir.BOTH,
            title_up="RPC traffic increase",
            title_down="RPC traffic drop",
        ),
        Rule(
            "rpc_error_ratio",
            F.REQUEST_FAILURES,
            "RPC failure rate (status != OK)",
            thresholds="rpc_error_ratio",
            volume_guard=True,
        ),
        Rule(
            "rpc_latency_p95",
            F.LATENCY,
            "RPC p95 latency",
            thresholds="latency_quantile",
            volume_guard=True,
        ),
        Rule(
            "proxy_connections_active",
            F.PROXY,
            "Open proxy connections",
            thresholds="proxy_connections_active",
            severity_cap=Severity.MEDIUM,
            title_up="Open proxy connections above expected range",
        ),
        Rule(
            "proxy_connections_dropped",
            F.PROXY,
            "Dropped proxy connections",
            thresholds="proxy_connections_dropped",
        ),
        Rule(
            "proxy_down",
            F.PROXY,
            "Proxy status unavailable",
            kind=RuleKind.SHORTFALL,
            event_points=3,
            title_up="Proxy status unreadable by its exporter",
        ),
        Rule(
            "upstream_unavailable",
            F.PROXY,
            "Upstream unavailable",
            kind=RuleKind.SHORTFALL,
            event_points=3,
            title_up="Upstream server unavailable or unhealthy",
        ),
        Rule(
            "database_down",
            F.DATABASE,
            "Database unreachable",
            kind=RuleKind.SHORTFALL,
            event_points=3,
            title_up="Database unreachable by its exporter",
        ),
        Rule(
            "database_connections_ratio",
            F.DATABASE,
            "Connections vs max_connections",
            thresholds="database_connections_ratio",
        ),
        Rule(
            "database_replication_lag",
            F.DATABASE,
            "Replication lag",
            thresholds="database_replication_lag",
        ),
        Rule(
            "database_transaction_rate",
            F.DATABASE,
            "Transaction rate",
            thresholds="database_transaction_rate",
            direction=Dir.BOTH,
            severity_cap=Severity.MEDIUM,
            title_up="Transaction rate increase",
            title_down="Transaction rate drop",
        ),
        Rule(
            "database_rollback_ratio",
            F.DATABASE,
            "Rolled-back transactions",
            thresholds="database_rollback_ratio",
            volume_guard=True,
            volume_noun="transactions",
            title_up="Rollback share above expected range",
        ),
        Rule(
            "database_deadlocks",
            F.DATABASE,
            "Deadlocks",
            kind=RuleKind.EVENT,
            event_points=2,
            title_up="Deadlock detected",
        ),
        Rule(
            "database_temp_bytes",
            F.DATABASE,
            "Temporary file writes",
            thresholds="database_temp_bytes",
            severity_cap=Severity.MEDIUM,
            title_up="Temporary file writes above expected range (work_mem spills)",
        ),
        Rule(
            "database_longest_transaction",
            F.DATABASE,
            "Longest open transaction",
            thresholds="database_longest_transaction",
            title_up="Long-running transaction",
        ),
    )
}

SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
