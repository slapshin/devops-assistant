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

CATALOG_VERSION = "catalog-2026.10.1"

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
    traffic: str | None = None
    """Request-count signal that ranks this signal's routes (top N kept, the rest summed)."""
    description: str = ""
    labels_required: tuple[str, ...] = field(default=())

    @property
    def route_level(self) -> bool:
        return self.traffic is not None


def q(template: str, *metrics: str) -> QueryTemplate:
    return QueryTemplate(template, metrics)


NODE = ("job", "instance")
FS = ("job", "instance", "device", "mountpoint")
DEV = ("job", "instance", "device")
CONT = ("instance", "container")
SWARM = ("service_name",)
HTTP = ("job", "http_route", "http_request_method")
RPC = ("job", "rpc_method")

HTTP_TRAFFIC = "http_requests"
RPC_TRAFFIC = "rpc_requests"

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
        traffic=HTTP_TRAFFIC,
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
        traffic=HTTP_TRAFFIC,
    ),
    SignalDef(
        "http_4xx",
        SignalFamily.CLIENT_ERRORS,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(_http_rate('http_response_status_code=~"4.."'), _HTTP_COUNT),
        (_HTTP_COUNT,),
        traffic=HTTP_TRAFFIC,
    ),
    SignalDef(
        "http_404",
        SignalFamily.CLIENT_ERRORS,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.ROUTE,
        HTTP,
        q(_http_rate('http_response_status_code="404"'), _HTTP_COUNT),
        (_HTTP_COUNT,),
        traffic=HTTP_TRAFFIC,
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
        traffic=HTTP_TRAFFIC,
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
        traffic=HTTP_TRAFFIC,
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
        traffic=HTTP_TRAFFIC,
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
        traffic=RPC_TRAFFIC,
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
        traffic=RPC_TRAFFIC,
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
        traffic=RPC_TRAFFIC,
    ),
)


# --- reverse proxies -----------------------------------------------------------------------
# nginx: nginx-prometheus-exporter over stub_status (no status codes, no latency).
# Angie: the built-in prometheus module with the stock prometheus_all.conf template.
# Caddy: the built-in metrics endpoint; request counts come from the duration histogram, which
#   is the only request metric carrying `code`.
# Traefik: the built-in Prometheus exporter with service labels (the default).
NGINX = ("job", "instance")
ANGIE = ("job", "instance")
ANGIE_ZONE = ("job", "zone")
ANGIE_PEER = ("job", "upstream", "peer")
CADDY = ("job", "server", "handler")
CADDY_UPSTREAM = ("job", "upstream")
TRAEFIK = ("job", "service")
TRAEFIK_ENTRYPOINT = ("job", "entrypoint")
TRAEFIK_SERVER = ("job", "service", "url")

ANGIE_TRAFFIC = "angie_requests"
CADDY_TRAFFIC = "caddy_requests"
TRAEFIK_TRAFFIC = "traefik_requests"

_ANGIE_REQUESTS = "angie_http_server_zones_requests_total"
_ANGIE_RESPONSES = "angie_http_server_zones_responses"
_CADDY_COUNT = "caddy_http_request_duration_seconds_count"
_CADDY_BUCKET = "caddy_http_request_duration_seconds_bucket"
_TRAEFIK_COUNT = "traefik_service_requests_total"
_TRAEFIK_BUCKET = "traefik_service_request_duration_seconds_bucket"

# Angie peer states (prometheus_all.conf): 3 = unavailable (max_fails reached), 5 = unhealthy
# (failed active health check). down (2) is configured by the operator, so it is not a failure.
_ANGIE_PEER_FAILED_STATES = (3, 5)

_CADDY_BUCKET_GATE = Gate(
    q(f"count({_CADDY_BUCKET}{{{{{{s}}}}}})", _CADDY_BUCKET),
    "No histogram buckets for caddy_http_request_duration_seconds; percentiles unavailable.",
)
_TRAEFIK_BUCKET_GATE = Gate(
    q(f"count({_TRAEFIK_BUCKET}{{{{{{s}}}}}})", _TRAEFIK_BUCKET),
    "No histogram buckets for traefik_service_request_duration_seconds; percentiles unavailable.",
)


def _rate_by(metric: str, by: tuple[str, ...], matcher: str = "") -> str:
    extra = f", {matcher}" if matcher else ""
    return f"sum by ({', '.join(by)}) (rate({metric}{{{{{{s}}{extra}}}}}[{{w}}]))"


def _proxy_request_signals(
    prefix: str,
    traffic: str,
    identity: tuple[str, ...],
    total: str,
    responses: str,
    code_label: str,
) -> tuple[SignalDef, ...]:
    """Request rate plus 5xx/4xx/404 operands for one proxy, all on the same identity."""

    def status(signal: str, family: SignalFamily, matcher: str, role: Role) -> SignalDef:
        return SignalDef(
            f"{prefix}_{signal}",
            family,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.PROXY,
            identity,
            q(_rate_by(responses, identity, matcher), responses),
            (responses,),
            role=role,
            traffic=traffic,
        )

    return (
        SignalDef(
            traffic,
            SignalFamily.REQUEST_TRAFFIC,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.PROXY,
            identity,
            q(_rate_by(total, identity), total),
            (total,),
            direction=Direction.BOTH,
            traffic=traffic,
        ),
        status("5xx", SignalFamily.REQUEST_FAILURES, f'{code_label}=~"5.."', Role.OPERAND),
        status("4xx", SignalFamily.CLIENT_ERRORS, f'{code_label}=~"4.."', Role.SIGNAL),
        status("404", SignalFamily.CLIENT_ERRORS, f'{code_label}="404"', Role.SIGNAL),
    )


def _proxy_quantiles(
    prefix: str, traffic: str, identity: tuple[str, ...], bucket: str, gate: Gate
) -> tuple[SignalDef, ...]:
    by = ", ".join(identity)
    return tuple(
        SignalDef(
            f"{prefix}_latency_p{int(phi * 100)}",
            SignalFamily.LATENCY,
            Unit.SECONDS,
            EntityKind.PROXY,
            identity,
            q(_quantile(phi, bucket, by), bucket),
            (bucket,),
            gates=(gate,),
            traffic=traffic,
        )
        for phi in (0.95, 0.99)
    )


def _active_connections(prefix: str, metric: str, identity: tuple[str, ...]) -> SignalDef:
    return SignalDef(
        f"{prefix}_connections_active",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.PROXY,
        identity,
        q(f"sum by ({', '.join(identity)}) ({metric}{{{{{{s}}}}}})", metric),
        (metric,),
        description="Open client connections.",
    )


PROXY_CATALOG: tuple[SignalDef, ...] = (
    # --- nginx (stub_status) --------------------------------------------------------------
    SignalDef(
        "nginx_requests",
        SignalFamily.REQUEST_TRAFFIC,
        Unit.REQUESTS_PER_SECOND,
        EntityKind.PROXY,
        NGINX,
        q(_rate_by("nginx_http_requests_total", NGINX), "nginx_http_requests_total"),
        ("nginx_http_requests_total",),
        direction=Direction.BOTH,
        description="All requests; stub_status has no status codes or latency.",
    ),
    _active_connections("nginx", "nginx_connections_active", NGINX),
    SignalDef(
        "nginx_connections_dropped",
        SignalFamily.PROXY,
        Unit.PER_SECOND,
        EntityKind.PROXY,
        NGINX,
        q(
            _rate_by("nginx_connections_accepted", NGINX)
            + " - "
            + _rate_by("nginx_connections_handled", NGINX),
            "nginx_connections_accepted",
            "nginx_connections_handled",
        ),
        ("nginx_connections_accepted", "nginx_connections_handled"),
        description="Accepted minus handled connections (worker_connections or fd limits).",
    ),
    SignalDef(
        "nginx_down",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.PROXY,
        NGINX,
        q("1 - max by (job, instance) (nginx_up{{{s}}})", "nginx_up"),
        ("nginx_up",),
        description="1 while the exporter cannot read stub_status.",
    ),
    # --- Angie (prometheus_all.conf) ------------------------------------------------------
    *_proxy_request_signals(
        "angie", ANGIE_TRAFFIC, ANGIE_ZONE, _ANGIE_REQUESTS, _ANGIE_RESPONSES, "code"
    ),
    _active_connections("angie", "angie_connections_active", ANGIE),
    SignalDef(
        "angie_connections_dropped",
        SignalFamily.PROXY,
        Unit.PER_SECOND,
        EntityKind.PROXY,
        ANGIE,
        q(_rate_by("angie_connections_dropped", ANGIE), "angie_connections_dropped"),
        ("angie_connections_dropped",),
    ),
    SignalDef(
        "angie_peer_unavailable",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.UPSTREAM,
        ANGIE_PEER,
        q(
            "max by (job, upstream, peer) ("
            + " + ".join(
                f"(angie_http_upstreams_peers_state{{{{{{s}}}}}} == bool {state})"
                for state in _ANGIE_PEER_FAILED_STATES
            )
            + ")",
            "angie_http_upstreams_peers_state",
        ),
        ("angie_http_upstreams_peers_state",),
        description="1 while the peer is unavailable or unhealthy.",
    ),
    # --- Caddy ----------------------------------------------------------------------------
    *_proxy_request_signals("caddy", CADDY_TRAFFIC, CADDY, _CADDY_COUNT, _CADDY_COUNT, "code"),
    *_proxy_quantiles("caddy", CADDY_TRAFFIC, CADDY, _CADDY_BUCKET, _CADDY_BUCKET_GATE),
    SignalDef(
        "caddy_upstream_unhealthy",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.UPSTREAM,
        CADDY_UPSTREAM,
        q(
            "1 - min by (job, upstream) (caddy_reverse_proxy_upstreams_healthy{{{s}}})",
            "caddy_reverse_proxy_upstreams_healthy",
        ),
        ("caddy_reverse_proxy_upstreams_healthy",),
        description="1 while any Caddy instance reports the upstream unhealthy.",
    ),
    # --- Traefik --------------------------------------------------------------------------
    *_proxy_request_signals(
        "traefik", TRAEFIK_TRAFFIC, TRAEFIK, _TRAEFIK_COUNT, _TRAEFIK_COUNT, "code"
    ),
    *_proxy_quantiles("traefik", TRAEFIK_TRAFFIC, TRAEFIK, _TRAEFIK_BUCKET, _TRAEFIK_BUCKET_GATE),
    _active_connections("traefik", "traefik_open_connections", TRAEFIK_ENTRYPOINT),
    SignalDef(
        "traefik_server_down",
        SignalFamily.PROXY,
        Unit.COUNT,
        EntityKind.UPSTREAM,
        TRAEFIK_SERVER,
        q(
            "1 - min by (job, service, url) (traefik_service_server_up{{{s}}})",
            "traefik_service_server_up",
        ),
        ("traefik_service_server_up",),
        description="1 while any Traefik instance reports the server down.",
    ),
)

CATALOG = CATALOG + PROXY_CATALOG
TRAFFIC_SIGNALS = frozenset(d.traffic for d in CATALOG if d.traffic is not None)

BY_SIGNAL = {d.signal: d for d in CATALOG}
ALL_REQUIRED_METRICS = sorted({m for d in CATALOG for m in d.required_metrics})
