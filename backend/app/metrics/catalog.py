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

CATALOG_VERSION = "catalog-2026.10.4"

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

# --- PostgreSQL ----------------------------------------------------------------------------
# prometheus-community/postgres_exporter with its default collectors. `instance` is the exporter
# target (one PostgreSQL server); the pg_stat_database counters have no conventional suffix.
PG_SERVER = ("job", "instance")
PG_DB = ("job", "instance", "datname")
# Template databases never take connections. postgres_exporter skips the shared-objects row
# (no datname), so the empty alternative only guards older versions.
PG_DB_FILTER = 'datname!~"|template[01]"'
PG_TRAFFIC = "pg_transactions"

_PG_COMMIT = "pg_stat_database_xact_commit"
_PG_ROLLBACK = "pg_stat_database_xact_rollback"


def _pg_rate(metric: str, fn: str = "rate") -> str:
    by = ", ".join(PG_DB)
    return f"sum by ({by}) ({fn}({metric}{{{{{{s}}, {PG_DB_FILTER}}}}}[{{w}}]))"


POSTGRES_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "pg_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        PG_SERVER,
        q("1 - max by (job, instance) (pg_up{{{s}}})", "pg_up"),
        ("pg_up",),
        description="1 while the exporter cannot connect to PostgreSQL.",
    ),
    SignalDef(
        "pg_connections_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        PG_SERVER,
        q(
            "sum by (job, instance) (pg_stat_database_numbackends{{{s}}})"
            " / max by (job, instance) (pg_settings_max_connections{{{s}}} > 0)",
            "pg_stat_database_numbackends",
            "pg_settings_max_connections",
        ),
        ("pg_stat_database_numbackends", "pg_settings_max_connections"),
        description="Backends connected to any database vs max_connections.",
    ),
    SignalDef(
        "pg_replication_lag",
        SignalFamily.DATABASE,
        Unit.SECONDS,
        EntityKind.DATABASE,
        PG_SERVER,
        q(
            "max by (job, instance) (pg_replication_lag_seconds{{{s}}})",
            "pg_replication_lag_seconds",
        ),
        ("pg_replication_lag_seconds",),
        description="Replay lag on a standby; the exporter reports 0 on a primary.",
    ),
    SignalDef(
        PG_TRAFFIC,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        PG_DB,
        q(f"{_pg_rate(_PG_COMMIT)} + {_pg_rate(_PG_ROLLBACK)}", _PG_COMMIT, _PG_ROLLBACK),
        (_PG_COMMIT, _PG_ROLLBACK),
        direction=Direction.BOTH,
        description="Committed plus rolled-back transactions per second.",
    ),
    SignalDef(
        "pg_rollbacks",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        PG_DB,
        q(_pg_rate(_PG_ROLLBACK), _PG_ROLLBACK),
        (_PG_ROLLBACK,),
        role=Role.OPERAND,
    ),
    SignalDef(
        "pg_deadlocks",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        PG_DB,
        q(_pg_rate("pg_stat_database_deadlocks", "increase"), "pg_stat_database_deadlocks"),
        ("pg_stat_database_deadlocks",),
        description="Deadlocks detected per step.",
    ),
    SignalDef(
        "pg_temp_bytes",
        SignalFamily.DATABASE,
        Unit.BYTES_PER_SECOND,
        EntityKind.DATABASE,
        PG_DB,
        q(_pg_rate("pg_stat_database_temp_bytes"), "pg_stat_database_temp_bytes"),
        ("pg_stat_database_temp_bytes",),
        description="Temporary file writes (sorts and hashes spilling past work_mem).",
    ),
    SignalDef(
        "pg_longest_transaction",
        SignalFamily.DATABASE,
        Unit.SECONDS,
        EntityKind.DATABASE,
        PG_DB,
        q(
            f"max by ({', '.join(PG_DB)}) "
            f"(pg_stat_activity_max_tx_duration{{{{{{s}}, {PG_DB_FILTER}}}}})",
            "pg_stat_activity_max_tx_duration",
        ),
        ("pg_stat_activity_max_tx_duration",),
        description="Age of the oldest open transaction, including idle in transaction.",
    ),
)

# --- MySQL ---------------------------------------------------------------------------------
# prom/mysqld-exporter with its default collectors (global_status, global_variables,
# slave_status). `instance` is the exporter target (one MySQL server) and every signal is
# server-wide. SHOW GLOBAL STATUS counters have no conventional suffix.
MYSQL = ("job", "instance")
MYSQL_TRAFFIC = "mysql_queries"

_MYSQL_QUESTIONS = "mysql_global_status_questions"
_MYSQL_SLOW = "mysql_global_status_slow_queries"
_MYSQL_CONN_ERRORS = "mysql_global_status_connection_errors_total"


def _mysql_rate(metric: str, fn: str = "rate", extra: str = "") -> str:
    extra = f", {extra}" if extra else ""
    return f"sum by (job, instance) ({fn}({metric}{{{{{{s}}{extra}}}}}[{{w}}]))"


def _mysql_replication(variant: str, source: str) -> tuple[SignalDef, SignalDef]:
    """Lag and thread state, named after SHOW SLAVE STATUS or SHOW REPLICA STATUS columns.

    The exporter lower-cases column names: MySQL 8.4 dropped SHOW SLAVE STATUS, so its replicas
    export ``replica_*_running`` and ``seconds_behind_source`` instead.
    """
    lag = f"mysql_slave_status_seconds_behind_{source}"
    sql = f"mysql_slave_status_{variant}_sql_running"
    io = f"mysql_slave_status_{variant}_io_running"
    return (
        SignalDef(
            f"mysql_{variant}_lag",
            SignalFamily.DATABASE,
            Unit.SECONDS,
            EntityKind.DATABASE,
            MYSQL,
            q(f"max by (job, instance) ({lag}{{{{{{s}}}}}})", lag),
            (lag,),
            description="Replica lag; absent while the SQL thread is stopped.",
        ),
        SignalDef(
            f"mysql_{variant}_stopped",
            SignalFamily.DATABASE,
            Unit.COUNT,
            EntityKind.DATABASE,
            MYSQL,
            q(f"1 - min by (job, instance) ({sql}{{{{{{s}}}}}} * {io}{{{{{{s}}}}}})", sql, io),
            (sql, io),
            description="1 while the SQL or I/O thread is not running (incl. Connecting).",
        ),
    )


MYSQL_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "mysql_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        MYSQL,
        q("1 - max by (job, instance) (mysql_up{{{s}}})", "mysql_up"),
        ("mysql_up",),
        description="1 while the exporter cannot connect to MySQL.",
    ),
    SignalDef(
        "mysql_connections_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        MYSQL,
        q(
            "max by (job, instance) (mysql_global_status_threads_connected{{{s}}})"
            " / max by (job, instance) (mysql_global_variables_max_connections{{{s}}} > 0)",
            "mysql_global_status_threads_connected",
            "mysql_global_variables_max_connections",
        ),
        ("mysql_global_status_threads_connected", "mysql_global_variables_max_connections"),
        description="Open client connections vs max_connections.",
    ),
    SignalDef(
        "mysql_connections_refused",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        MYSQL,
        q(
            _mysql_rate(_MYSQL_CONN_ERRORS, "increase", 'error="max_connections"'),
            _MYSQL_CONN_ERRORS,
        ),
        (_MYSQL_CONN_ERRORS,),
        description="Connections refused per step because max_connections was reached.",
    ),
    *_mysql_replication("slave", "master"),
    *_mysql_replication("replica", "source"),
    SignalDef(
        MYSQL_TRAFFIC,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(_mysql_rate(_MYSQL_QUESTIONS), _MYSQL_QUESTIONS),
        (_MYSQL_QUESTIONS,),
        direction=Direction.BOTH,
        description="Statements sent by clients per second (Questions).",
    ),
    SignalDef(
        "mysql_slow_queries",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(_mysql_rate(_MYSQL_SLOW), _MYSQL_SLOW),
        (_MYSQL_SLOW,),
        role=Role.OPERAND,
    ),
    SignalDef(
        "mysql_row_lock_waits",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(
            _mysql_rate("mysql_global_status_innodb_row_lock_waits"),
            "mysql_global_status_innodb_row_lock_waits",
        ),
        ("mysql_global_status_innodb_row_lock_waits",),
        description="InnoDB row lock waits per second.",
    ),
    SignalDef(
        "mysql_tmp_disk_tables",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(
            _mysql_rate("mysql_global_status_created_tmp_disk_tables"),
            "mysql_global_status_created_tmp_disk_tables",
        ),
        ("mysql_global_status_created_tmp_disk_tables",),
        description="Internal temporary tables created on disk per second.",
    ),
)

# --- Redis ---------------------------------------------------------------------------------
# oliver006/redis_exporter with its defaults (INFO, CONFIG GET maxclients/maxmemory,
# commandstats). `instance` is one Redis server: the exporter address in single-target mode,
# or the target after the usual relabelling in multi-target mode. Every signal is server-wide.
REDIS = ("job", "instance")
REDIS_TRAFFIC = "redis_commands"
REDIS_LOOKUPS = "redis_keyspace_lookups"

_REDIS_HITS = "redis_keyspace_hits_total"
_REDIS_MISSES = "redis_keyspace_misses_total"
_REDIS_CALLS = "redis_commands_total"
_REDIS_CALL_SECONDS = "redis_commands_duration_seconds_total"


def _redis_rate(metric: str, fn: str = "rate") -> str:
    return f"sum by (job, instance) ({fn}({metric}{{{{{{s}}}}}}[{{w}}]))"


REDIS_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "redis_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q("1 - max by (job, instance) (redis_up{{{s}}})", "redis_up"),
        ("redis_up",),
        description="1 while the exporter cannot connect to Redis.",
    ),
    SignalDef(
        "redis_clients_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        REDIS,
        q(
            "max by (job, instance) (redis_connected_clients{{{s}}})"
            " / (max by (job, instance) (redis_max_clients{{{s}}} > 0)"
            " or max by (job, instance) (redis_config_maxclients{{{s}}} > 0))",
            "redis_connected_clients",
            "redis_max_clients",
            "redis_config_maxclients",
        ),
        ("redis_connected_clients",),
        gates=(
            Gate(
                q(
                    "count(redis_max_clients{{{s}}} > 0 or redis_config_maxclients{{{s}}} > 0)",
                    "redis_max_clients",
                    "redis_config_maxclients",
                ),
                "maxclients is not exported (INFO needs Redis 7+, CONFIG GET may be disabled).",
            ),
        ),
        description="Connected clients vs maxclients (INFO, else CONFIG GET).",
    ),
    SignalDef(
        "redis_rejected_connections",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q(
            _redis_rate("redis_rejected_connections_total", "increase"),
            "redis_rejected_connections_total",
        ),
        ("redis_rejected_connections_total",),
        description="Connections rejected per step because maxclients was reached.",
    ),
    SignalDef(
        "redis_memory_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        REDIS,
        q(
            "max by (job, instance) (redis_memory_used_bytes{{{s}}})"
            " / max by (job, instance) (redis_memory_max_bytes{{{s}}} > 0)",
            "redis_memory_used_bytes",
            "redis_memory_max_bytes",
        ),
        ("redis_memory_used_bytes", "redis_memory_max_bytes"),
        gates=(
            Gate(
                q("count(redis_memory_max_bytes{{{s}}} > 0)", "redis_memory_max_bytes"),
                "maxmemory is not set (0) on any Redis server.",
            ),
        ),
        description="used_memory vs maxmemory; servers without maxmemory are skipped.",
    ),
    SignalDef(
        "redis_evictions",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(_redis_rate("redis_evicted_keys_total"), "redis_evicted_keys_total"),
        ("redis_evicted_keys_total",),
        description="Keys evicted per second under the maxmemory policy.",
    ),
    SignalDef(
        "redis_replica_link_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q("1 - min by (job, instance) (redis_master_link_up{{{s}}})", "redis_master_link_up"),
        ("redis_master_link_up",),
        description="1 while a replica's link to its master is down; absent on masters.",
    ),
    SignalDef(
        "redis_persistence_failed",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q(
            "1 - min by (job, instance) (redis_rdb_last_bgsave_status{{{s}}}"
            " * redis_aof_last_write_status{{{s}}})",
            "redis_rdb_last_bgsave_status",
            "redis_aof_last_write_status",
        ),
        ("redis_rdb_last_bgsave_status", "redis_aof_last_write_status"),
        description="1 while the last RDB snapshot or AOF write failed.",
    ),
    SignalDef(
        REDIS_TRAFFIC,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(_redis_rate("redis_commands_processed_total"), "redis_commands_processed_total"),
        ("redis_commands_processed_total",),
        direction=Direction.BOTH,
        description="Commands processed per second.",
    ),
    SignalDef(
        "redis_command_latency_mean",
        SignalFamily.DATABASE,
        Unit.SECONDS,
        EntityKind.DATABASE,
        REDIS,
        q(
            f"{_redis_rate(_REDIS_CALL_SECONDS)} / ({_redis_rate(_REDIS_CALLS)} > 0)",
            _REDIS_CALL_SECONDS,
            _REDIS_CALLS,
        ),
        (_REDIS_CALL_SECONDS, _REDIS_CALLS),
        description="Mean server-side execution time over all commands (commandstats).",
    ),
    SignalDef(
        REDIS_LOOKUPS,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(f"{_redis_rate(_REDIS_HITS)} + {_redis_rate(_REDIS_MISSES)}", _REDIS_HITS, _REDIS_MISSES),
        (_REDIS_HITS, _REDIS_MISSES),
        role=Role.OPERAND,
    ),
    SignalDef(
        "redis_keyspace_misses",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(_redis_rate(_REDIS_MISSES), _REDIS_MISSES),
        (_REDIS_MISSES,),
        role=Role.OPERAND,
    ),
)

CATALOG = CATALOG + PROXY_CATALOG + POSTGRES_CATALOG + MYSQL_CATALOG + REDIS_CATALOG
TRAFFIC_SIGNALS = frozenset(d.traffic for d in CATALOG if d.traffic is not None)

BY_SIGNAL = {d.signal: d for d in CATALOG}
ALL_REQUIRED_METRICS = sorted({m for d in CATALOG for m in d.required_metrics})
