"""Versioned catalog of scoped signal queries (T004).

Conventions:
- ``rate``/``increase`` are applied to each raw counter before any aggregation, so counter
  resets are handled per series by the source.
- Ratios are returned only where their operands are the same entity; HTTP/RPC failure ratios
  are *not* computed in PromQL: numerator and denominator series are collected separately so
  the analysis can apply the minimum-volume rule and treat an absent numerator as zero only
  where the denominator was observed.
- Exclusion rules (pseudo filesystems, virtual devices, unnamed cgroups) are constants below,
  documented in docs/metrics-catalog.md.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.common import EntityKind, SignalFamily, Unit
from app.metrics.promql import QueryTemplate

CATALOG_VERSION = "catalog-2026.09.1"

FS_FILTER = 'fstype=~"ext[234]|xfs|btrfs|zfs", mountpoint!~"/(run|dev|sys|proc)($|/).*"'
DISK_FILTER = 'device!~"(sr|loop|ram|fd)[0-9]+"'
NET_FILTER = 'device!~"lo|veth.*|docker.*|br-.*|virbr.*|cali.*|flannel.*|cni.*"'
CONTAINER_FILTER = 'name!=""'
# Swarm task containers are named "<service>.<slot or node id>.<25-char task id>"; dropping the
# task id gives an identity that survives task replacement.
CONTAINER_KEY_REGEX = "(.+?)(\\\\.[a-z0-9]{{25}})?"


def _container(inner: str) -> str:
    return f'label_replace({inner}, "container", "$1", "name", "{CONTAINER_KEY_REGEX}")'


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    BOTH = "both"


class Role(StrEnum):
    SIGNAL = "signal"
    """Analysed directly."""
    OPERAND = "operand"
    """Numerator/denominator for a ratio derived in the analysis."""


@dataclass(frozen=True)
class Gate:
    """Instant query that must return a positive value for the signal to be supported."""

    template: QueryTemplate
    unsupported_reason: str


@dataclass(frozen=True)
class SignalDef:
    signal: str
    family: SignalFamily
    unit: Unit
    entity_kind: EntityKind
    identity: tuple[str, ...]
    query: QueryTemplate
    required_metrics: tuple[str, ...]
    direction: Direction = Direction.UP
    role: Role = Role.SIGNAL
    gates: tuple[Gate, ...] = ()
    route_level: bool = False
    description: str = ""
    labels_required: tuple[str, ...] = field(default=())


def q(template: str, *metrics: str) -> QueryTemplate:
    return QueryTemplate(template, metrics)


NODE = ("job", "instance")
FS = ("job", "instance", "device", "mountpoint")
DEV = ("job", "instance", "device")
CONT = ("instance", "container")
SWARM = ("service_name",)
HTTP = ("job", "http_route", "http_request_method")
RPC = ("job", "rpc_method")

_HTTP_COUNT = "http_server_request_duration_seconds_count"
_HTTP_BUCKET = "http_server_request_duration_seconds_bucket"
_HTTP_SUM = "http_server_request_duration_seconds_sum"
_RPC_COUNT = "rpc_server_call_duration_seconds_count"
_RPC_BUCKET = "rpc_server_call_duration_seconds_bucket"


def _http_rate(status: str = "") -> str:
    extra = f", {status}" if status else ""
    return (
        f"sum by (job, http_route, http_request_method) "
        f"(rate({_HTTP_COUNT}{{{{{{s}}{extra}}}}}[{{w}}]))"
    )


def _quantile(phi: float, bucket: str, by: str) -> str:
    return f"histogram_quantile({phi}, sum by ({by}, le) (rate({bucket}{{{{{{s}}}}}}[{{w}}])))"


_HTTP_BUCKET_GATE = Gate(
    q(f"count({_HTTP_BUCKET}{{{{{{s}}}}}})", _HTTP_BUCKET),
    "No histogram buckets for http_server_request_duration_seconds; percentiles unavailable.",
)
_RPC_BUCKET_GATE = Gate(
    q(f"count({_RPC_BUCKET}{{{{{{s}}}}}})", _RPC_BUCKET),
    "No histogram buckets for rpc_server_call_duration_seconds; percentiles unavailable.",
)

CATALOG: tuple[SignalDef, ...] = (
    # --- node CPU / memory -----------------------------------------------------------------
    SignalDef(
        "cpu_utilization",
        SignalFamily.CPU,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            '1 - avg by (job, instance) (rate(node_cpu_seconds_total{{{s}, mode="idle"}}[{w}]))',
            "node_cpu_seconds_total",
        ),
        ("node_cpu_seconds_total",),
        description="Non-idle CPU share (all cores).",
    ),
    SignalDef(
        "cpu_iowait",
        SignalFamily.CPU,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            'avg by (job, instance) (rate(node_cpu_seconds_total{{{s}, mode="iowait"}}[{w}]))',
            "node_cpu_seconds_total",
        ),
        ("node_cpu_seconds_total",),
        description="CPU share waiting on I/O.",
    ),
    SignalDef(
        "memory_utilization",
        SignalFamily.MEMORY,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            "1 - max by (job, instance) (node_memory_MemAvailable_bytes{{{s}}})"
            " / max by (job, instance) (node_memory_MemTotal_bytes{{{s}}} > 0)",
            "node_memory_MemAvailable_bytes",
            "node_memory_MemTotal_bytes",
        ),
        ("node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes"),
        description="1 - MemAvailable/MemTotal.",
    ),
    SignalDef(
        "memory_pressure",
        SignalFamily.MEMORY,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            "max by (job, instance) (rate(node_pressure_memory_waiting_seconds_total{{{s}}}[{w}]))",
            "node_pressure_memory_waiting_seconds_total",
        ),
        ("node_pressure_memory_waiting_seconds_total",),
        description="PSI: share of time tasks waited on memory.",
    ),
    SignalDef(
        "swap_used_ratio",
        SignalFamily.MEMORY,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            "1 - max by (job, instance) (node_memory_SwapFree_bytes{{{s}}})"
            " / max by (job, instance) (node_memory_SwapTotal_bytes{{{s}}} > 0)",
            "node_memory_SwapFree_bytes",
            "node_memory_SwapTotal_bytes",
        ),
        ("node_memory_SwapFree_bytes", "node_memory_SwapTotal_bytes"),
        gates=(
            Gate(
                q("count(node_memory_SwapTotal_bytes{{{s}}} > 0)", "node_memory_SwapTotal_bytes"),
                "Swap is not configured (SwapTotal is 0 on every node).",
            ),
        ),
    ),
    SignalDef(
        "node_oom_kills",
        SignalFamily.MEMORY,
        Unit.COUNT,
        EntityKind.NODE,
        NODE,
        q(
            "max by (job, instance) (increase(node_vmstat_oom_kill{{{s}}}[{w}]))",
            "node_vmstat_oom_kill",
        ),
        ("node_vmstat_oom_kill",),
        description="Kernel OOM kills per step.",
    ),
    SignalDef(
        "io_pressure",
        SignalFamily.DISK_IO,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            "max by (job, instance) (rate(node_pressure_io_waiting_seconds_total{{{s}}}[{w}]))",
            "node_pressure_io_waiting_seconds_total",
        ),
        ("node_pressure_io_waiting_seconds_total",),
        description="PSI: share of time tasks waited on I/O.",
    ),
    # --- filesystems / disks / network ----------------------------------------------------
    SignalDef(
        "filesystem_used_ratio",
        SignalFamily.FILESYSTEM,
        Unit.RATIO,
        EntityKind.FILESYSTEM,
        FS,
        q(
            "1 - max by (job, instance, device, mountpoint) (node_filesystem_avail_bytes{{{s}, "
            + FS_FILTER
            + "}}) / max by (job, instance, device, mountpoint) "
            "(node_filesystem_size_bytes{{{s}, " + FS_FILTER + "}} > 0)",
            "node_filesystem_avail_bytes",
            "node_filesystem_size_bytes",
        ),
        ("node_filesystem_avail_bytes", "node_filesystem_size_bytes"),
    ),
    SignalDef(
        "filesystem_inodes_used_ratio",
        SignalFamily.FILESYSTEM,
        Unit.RATIO,
        EntityKind.FILESYSTEM,
        FS,
        q(
            "1 - max by (job, instance, device, mountpoint) (node_filesystem_files_free{{{s}, "
            + FS_FILTER
            + "}}) / max by (job, instance, device, mountpoint) "
            "(node_filesystem_files{{{s}, " + FS_FILTER + "}} > 0)",
            "node_filesystem_files_free",
            "node_filesystem_files",
        ),
        ("node_filesystem_files_free", "node_filesystem_files"),
    ),
    SignalDef(
        "disk_busy_ratio",
        SignalFamily.DISK_IO,
        Unit.RATIO,
        EntityKind.DISK,
        DEV,
        q(
            "max by (job, instance, device) (rate(node_disk_io_time_seconds_total{{{s}, "
            + DISK_FILTER
            + "}}[{w}]))",
            "node_disk_io_time_seconds_total",
        ),
        ("node_disk_io_time_seconds_total",),
    ),
    SignalDef(
        "disk_io_bytes",
        SignalFamily.DISK_IO,
        Unit.BYTES_PER_SECOND,
        EntityKind.DISK,
        DEV,
        q(
            "sum by (job, instance, device) (rate("
            '{{__name__=~"node_disk_(read|written)_bytes_total", '
            "{s}, " + DISK_FILTER + "}}[{w}]))"
        ),
        ("node_disk_read_bytes_total", "node_disk_written_bytes_total"),
        direction=Direction.BOTH,
    ),
    SignalDef(
        "network_receive_bytes",
        SignalFamily.NETWORK,
        Unit.BYTES_PER_SECOND,
        EntityKind.NETWORK_INTERFACE,
        DEV,
        q(
            "sum by (job, instance, device) (rate(node_network_receive_bytes_total{{{s}, "
            + NET_FILTER
            + "}}[{w}]))",
            "node_network_receive_bytes_total",
        ),
        ("node_network_receive_bytes_total",),
        direction=Direction.BOTH,
    ),
    SignalDef(
        "network_transmit_bytes",
        SignalFamily.NETWORK,
        Unit.BYTES_PER_SECOND,
        EntityKind.NETWORK_INTERFACE,
        DEV,
        q(
            "sum by (job, instance, device) (rate(node_network_transmit_bytes_total{{{s}, "
            + NET_FILTER
            + "}}[{w}]))",
            "node_network_transmit_bytes_total",
        ),
        ("node_network_transmit_bytes_total",),
        direction=Direction.BOTH,
    ),
    SignalDef(
        "network_errors",
        SignalFamily.NETWORK,
        Unit.PER_SECOND,
        EntityKind.NETWORK_INTERFACE,
        DEV,
        q(
            "sum by (job, instance, device) (rate("
            '{{__name__=~"node_network_(receive|transmit)_(errs|drop)_total", {s}, '
            + NET_FILTER
            + "}}[{w}]))"
        ),
        (
            "node_network_receive_errs_total",
            "node_network_transmit_errs_total",
            "node_network_receive_drop_total",
            "node_network_transmit_drop_total",
        ),
        description="Interface errors + drops, rx + tx.",
    ),
    # --- containers (cAdvisor) ------------------------------------------------------------
    SignalDef(
        "container_cpu",
        SignalFamily.CONTAINER,
        Unit.PER_SECOND,
        EntityKind.CONTAINER,
        CONT,
        q(
            "sum by (instance, container) ("
            + _container(
                "rate(container_cpu_usage_seconds_total{{{s}, " + CONTAINER_FILTER + "}}[{w}])"
            )
            + ")",
            "container_cpu_usage_seconds_total",
        ),
        ("container_cpu_usage_seconds_total",),
        description="CPU cores used.",
    ),
    SignalDef(
        "container_memory_working_set",
        SignalFamily.CONTAINER,
        Unit.BYTES,
        EntityKind.CONTAINER,
        CONT,
        q(
            "max by (instance, container) ("
            + _container("container_memory_working_set_bytes{{{s}, " + CONTAINER_FILTER + "}}")
            + ")",
            "container_memory_working_set_bytes",
        ),
        ("container_memory_working_set_bytes",),
    ),
    SignalDef(
        "container_memory_limit_ratio",
        SignalFamily.CONTAINER,
        Unit.RATIO,
        EntityKind.CONTAINER,
        CONT,
        q(
            "max by (instance, container) ("
            + _container("container_memory_working_set_bytes{{{s}, " + CONTAINER_FILTER + "}}")
            + ") / max by (instance, container) ("
            + _container("container_spec_memory_limit_bytes{{{s}, " + CONTAINER_FILTER + "}} > 0")
            + ")",
            "container_memory_working_set_bytes",
            "container_spec_memory_limit_bytes",
        ),
        ("container_memory_working_set_bytes", "container_spec_memory_limit_bytes"),
        gates=(
            Gate(
                q(
                    "count(container_spec_memory_limit_bytes{{{s}, " + CONTAINER_FILTER + "}} > 0)",
                    "container_spec_memory_limit_bytes",
                ),
                "No container has a memory limit (container_spec_memory_limit_bytes is 0).",
            ),
        ),
    ),
    SignalDef(
        "container_throttling_ratio",
        SignalFamily.CONTAINER,
        Unit.RATIO,
        EntityKind.CONTAINER,
        CONT,
        q(
            "sum by (instance, container) ("
            + _container(
                "rate(container_cpu_cfs_throttled_periods_total{{{s}, "
                + CONTAINER_FILTER
                + "}}[{w}])"
            )
            + ") / (sum by (instance, container) ("
            + _container(
                "rate(container_cpu_cfs_periods_total{{{s}, " + CONTAINER_FILTER + "}}[{w}])"
            )
            + ") > 0)",
            "container_cpu_cfs_throttled_periods_total",
            "container_cpu_cfs_periods_total",
        ),
        ("container_cpu_cfs_throttled_periods_total", "container_cpu_cfs_periods_total"),
    ),
    SignalDef(
        "container_oom",
        SignalFamily.CONTAINER,
        Unit.COUNT,
        EntityKind.CONTAINER,
        CONT,
        q(
            "sum by (instance, container) ("
            + _container(
                "increase(container_oom_events_total{{{s}, " + CONTAINER_FILTER + "}}[{w}])"
            )
            + ")",
            "container_oom_events_total",
        ),
        ("container_oom_events_total",),
    ),
    # --- swarm task/replica state (restart proxy) -----------------------------------------
    SignalDef(
        "swarm_failed_tasks",
        SignalFamily.CONTAINER,
        Unit.COUNT,
        EntityKind.SERVICE,
        SWARM,
        q(
            'count by (service_name) (docker_swarm_task_info{{{s}, state=~"failed|rejected"}})',
            "docker_swarm_task_info",
        ),
        ("docker_swarm_task_info",),
        description="Failed/rejected tasks still listed by Swarm; a rise means new failures "
        "(task history is pruned, so decreases carry no meaning).",
    ),
    SignalDef(
        "swarm_replica_shortfall",
        SignalFamily.CONTAINER,
        Unit.COUNT,
        EntityKind.SERVICE,
        SWARM,
        q(
            "(max by (service_name) (docker_swarm_service_replicas_desired{{{s}}})"
            " - max by (service_name) (docker_swarm_service_replicas_running{{{s}}}))"
            " and on (service_name) max by (service_name) "
            '(docker_swarm_service_info{{{s}, mode="replicated"}})',
            "docker_swarm_service_replicas_desired",
            "docker_swarm_service_replicas_running",
            "docker_swarm_service_info",
        ),
        (
            "docker_swarm_service_replicas_desired",
            "docker_swarm_service_replicas_running",
            "docker_swarm_service_info",
        ),
        description="Desired minus running replicas for replicated services.",
    ),
    # --- HTTP ------------------------------------------------------------------------------
    SignalDef(
        "http_requests",
        SignalFamily.REQUEST_TRAFFIC,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(_http_rate(), _HTTP_COUNT),
        (_HTTP_COUNT,),
        direction=Direction.BOTH,
        route_level=True,
    ),
    SignalDef(
        "http_5xx",
        SignalFamily.REQUEST_FAILURES,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(_http_rate('http_response_status_code=~"5.."'), _HTTP_COUNT),
        (_HTTP_COUNT,),
        role=Role.OPERAND,
        route_level=True,
    ),
    SignalDef(
        "http_4xx",
        SignalFamily.CLIENT_ERRORS,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(_http_rate('http_response_status_code=~"4.."'), _HTTP_COUNT),
        (_HTTP_COUNT,),
        route_level=True,
    ),
    SignalDef(
        "http_404",
        SignalFamily.CLIENT_ERRORS,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(_http_rate('http_response_status_code="404"'), _HTTP_COUNT),
        (_HTTP_COUNT,),
        route_level=True,
    ),
    SignalDef(
        "http_latency_p95",
        SignalFamily.LATENCY,
        Unit.SECONDS,
        EntityKind.ROUTE,
        HTTP,
        q(_quantile(0.95, _HTTP_BUCKET, "job, http_route, http_request_method"), _HTTP_BUCKET),
        (_HTTP_BUCKET,),
        gates=(_HTTP_BUCKET_GATE,),
        route_level=True,
    ),
    SignalDef(
        "http_latency_p99",
        SignalFamily.LATENCY,
        Unit.SECONDS,
        EntityKind.ROUTE,
        HTTP,
        q(_quantile(0.99, _HTTP_BUCKET, "job, http_route, http_request_method"), _HTTP_BUCKET),
        (_HTTP_BUCKET,),
        gates=(_HTTP_BUCKET_GATE,),
        route_level=True,
    ),
    SignalDef(
        "http_latency_mean",
        SignalFamily.LATENCY,
        Unit.SECONDS,
        EntityKind.ROUTE,
        HTTP,
        q(
            "sum by (job, http_route, http_request_method) (rate(" + _HTTP_SUM + "{{{s}}}[{w}]))"
            " / (sum by (job, http_route, http_request_method) (rate("
            + _HTTP_COUNT
            + "{{{s}}}[{w}])) > 0)",
            _HTTP_SUM,
            _HTTP_COUNT,
        ),
        (_HTTP_SUM, _HTTP_COUNT),
        route_level=True,
        description="Mean latency; used only when histogram buckets are absent.",
    ),
    # --- RPC -------------------------------------------------------------------------------
    SignalDef(
        "rpc_requests",
        SignalFamily.REQUEST_TRAFFIC,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        RPC,
        q(f"sum by (job, rpc_method) (rate({_RPC_COUNT}{{{{{{s}}}}}}[{{w}}]))", _RPC_COUNT),
        (_RPC_COUNT,),
        direction=Direction.BOTH,
        route_level=True,
    ),
    SignalDef(
        "rpc_errors",
        SignalFamily.REQUEST_FAILURES,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        RPC,
        q(
            f"sum by (job, rpc_method) (rate({_RPC_COUNT}{{{{{{s}}, "
            f'rpc_response_status_code!="OK"}}}}[{{w}}]))',
            _RPC_COUNT,
        ),
        (_RPC_COUNT,),
        role=Role.OPERAND,
        route_level=True,
    ),
    SignalDef(
        "rpc_latency_p95",
        SignalFamily.LATENCY,
        Unit.SECONDS,
        EntityKind.ROUTE,
        RPC,
        q(_quantile(0.95, _RPC_BUCKET, "job, rpc_method"), _RPC_BUCKET),
        (_RPC_BUCKET,),
        gates=(_RPC_BUCKET_GATE,),
        route_level=True,
    ),
)

BY_SIGNAL = {d.signal: d for d in CATALOG}
ALL_REQUIRED_METRICS = sorted({m for d in CATALOG for m in d.required_metrics})
