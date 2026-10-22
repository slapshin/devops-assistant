"""SeaweedFS signals (the built-in metrics of master, volume, filer and S3 servers)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    Direction,
    RateQuery,
    Role,
    SignalDef,
    aggregate,
    latency_quantiles,
    q,
    rate,
    sel,
    sum_rate,
)

# `weed -metricsPort` on every server, scraped with one job per component (master, volume,
# filer; the S3 gateway usually runs inside the filer and shares its job). Request metrics are
# analysed per API, not per instance: replicas behind one endpoint share the load, and a
# per-instance split leaves too few requests per step. The API is not a label of the metrics,
# so `label_replace` adds it after aggregation; it keeps the S3 gateway and the filer apart
# when they share a job. Server health is per instance; disk usage per data directory.
SEAWEEDFS_API = ("job", "api")
SEAWEEDFS_SERVER = ("job", "instance")
SEAWEEDFS_DISK = ("job", "instance", "name")
SEAWEEDFS_CLUSTER = ("job",)

# API label value -> metric prefix of its request counter and duration histogram.
_APIS = {
    "s3": "SeaweedFS_s3_request",
    "filer": "SeaweedFS_filer_request",
    "volume": "SeaweedFS_volumeServer_request",
}
_STORE_API = "filer_store"
_STORE_PREFIX = "SeaweedFS_filerStore_request"

_RESOURCE = "SeaweedFS_volumeServer_resource"
_VOLUMES = "SeaweedFS_volumeServer_volumes"
_MAX_VOLUMES = "SeaweedFS_volumeServer_max_volumes"
_READ_ONLY = "SeaweedFS_volumeServer_read_only_volumes"
_IS_LEADER = "SeaweedFS_master_is_leader"
_HEARTBEATS = "SeaweedFS_master_received_heartbeats"
_PICK_ERRORS = "SeaweedFS_master_pick_for_write_error"
_FILER_HANDLER = "SeaweedFS_filer_handler_total"

# Filer handler outcomes that mean a failed chunk assignment, upload or download (not 404s).
_FILER_FAILURES = 'type=~".*([Ff]ailed|Error).*"'


def traffic_signal(api: str) -> str:
    return f"seaweedfs_{api}_requests"


def _api_rate(api: str) -> RateQuery:
    """Per-API rate sum: aggregate without the (absent) `api` label, then name the API."""

    def by_entity(metric: str, by: tuple[str, ...], *matchers: str) -> str:
        inner = sum_rate(metric, tuple(k for k in by if k != "api"), *matchers)
        return f'label_replace({inner}, "api", "{api}", "", "")'

    return by_entity


def _api_signals(api: str, prefix: str, *, codes: bool = True) -> tuple[SignalDef, ...]:
    """Request rate, 5xx/4xx/404 operands where the API has status codes, and p95/p99."""
    traffic = traffic_signal(api)
    total = f"{prefix}_total"
    by_entity = _api_rate(api)

    def status(signal: str, family: SignalFamily, matcher: str, role: Role) -> SignalDef:
        return SignalDef(
            f"seaweedfs_{api}_{signal}",
            family,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.STORAGE,
            SEAWEEDFS_API,
            q(by_entity(total, SEAWEEDFS_API, matcher), total),
            role=role,
            traffic=traffic,
        )

    statuses = (
        (
            status("5xx", SignalFamily.REQUEST_FAILURES, 'code=~"5.."', Role.OPERAND),
            status("4xx", SignalFamily.CLIENT_ERRORS, 'code=~"4.."', Role.SIGNAL),
            status("404", SignalFamily.CLIENT_ERRORS, 'code="404"', Role.SIGNAL),
        )
        if codes
        else ()
    )
    return (
        SignalDef(
            traffic,
            SignalFamily.REQUEST_TRAFFIC,
            Unit.REQUESTS_PER_SECOND,
            EntityKind.STORAGE,
            SEAWEEDFS_API,
            q(by_entity(total, SEAWEEDFS_API), total),
            direction=Direction.BOTH,
            traffic=traffic,
        ),
        *statuses,
        *latency_quantiles(
            f"seaweedfs_{api}",
            EntityKind.STORAGE,
            SEAWEEDFS_API,
            f"{prefix}_seconds_bucket",
            traffic,
            by_entity=by_entity,
        ),
    )


# Every SeaweedFS server exports the master's plain gauges and counters (0 off the masters);
# masters are the servers that received volume heartbeats.
_ON_MASTERS = "and on (job, instance) " + sel(_HEARTBEATS, 'type="total"')


def _resource(kind: str) -> str:
    return aggregate("max", SEAWEEDFS_DISK, sel(_RESOURCE, f'type="{kind}"'))


SEAWEEDFS_CATALOG: tuple[SignalDef, ...] = (
    *(signal for api, prefix in _APIS.items() for signal in _api_signals(api, prefix)),
    *_api_signals(_STORE_API, _STORE_PREFIX, codes=False),
    SignalDef(
        "seaweedfs_disk_used_ratio",
        SignalFamily.STORAGE,
        Unit.RATIO,
        EntityKind.STORAGE,
        SEAWEEDFS_DISK,
        q(f"1 - {_resource('free')} / ({_resource('all')} > 0)", _RESOURCE),
        description="Used share of the disk holding a volume server's data directory.",
    ),
    SignalDef(
        "seaweedfs_volume_slots_used_ratio",
        SignalFamily.STORAGE,
        Unit.RATIO,
        EntityKind.STORAGE,
        SEAWEEDFS_SERVER,
        q(
            f"{aggregate('sum', SEAWEEDFS_SERVER, sel(_VOLUMES, 'type="volume"'))}"
            f" / ({aggregate('max', SEAWEEDFS_SERVER, sel(_MAX_VOLUMES))} > 0)",
            _VOLUMES,
            _MAX_VOLUMES,
        ),
        description="Volumes held vs the volume server's maximum; no new volumes at the limit.",
    ),
    SignalDef(
        "seaweedfs_read_only_volumes",
        SignalFamily.STORAGE,
        Unit.COUNT,
        EntityKind.STORAGE,
        SEAWEEDFS_SERVER,
        q(aggregate("sum", SEAWEEDFS_SERVER, sel(_READ_ONLY, 'type="IsReadOnly"')), _READ_ONLY),
        description="Volumes that accept no writes (marked read-only or low on disk space).",
    ),
    SignalDef(
        "seaweedfs_disk_space_low_volumes",
        SignalFamily.STORAGE,
        Unit.COUNT,
        EntityKind.STORAGE,
        SEAWEEDFS_SERVER,
        q(
            aggregate("sum", SEAWEEDFS_SERVER, sel(_READ_ONLY, 'type="isDiskSpaceLow"')),
            _READ_ONLY,
        ),
        description="Volumes turned read-only because the disk fell below -minFreeSpace.",
    ),
    SignalDef(
        "seaweedfs_master_no_leader",
        SignalFamily.STORAGE,
        Unit.COUNT,
        EntityKind.STORAGE,
        SEAWEEDFS_CLUSTER,
        q(
            "1 - " + aggregate("max", SEAWEEDFS_CLUSTER, f"{sel(_IS_LEADER)} {_ON_MASTERS}"),
            _IS_LEADER,
            _HEARTBEATS,
        ),
        description="1 while no master of the cluster is the Raft leader.",
    ),
    SignalDef(
        "seaweedfs_assign_errors",
        SignalFamily.STORAGE,
        Unit.PER_SECOND,
        EntityKind.STORAGE,
        SEAWEEDFS_CLUSTER,
        q(
            aggregate("sum", SEAWEEDFS_CLUSTER, f"{rate(_PICK_ERRORS)} {_ON_MASTERS}"),
            _PICK_ERRORS,
            _HEARTBEATS,
        ),
        description="File ID assignments the master could not serve (no writable volume).",
    ),
    SignalDef(
        "seaweedfs_filer_failures",
        SignalFamily.STORAGE,
        Unit.PER_SECOND,
        EntityKind.STORAGE,
        SEAWEEDFS_CLUSTER,
        # A failure type gets a series only once it occurs: 0 where the filer handles anything.
        q(
            f"{sum_rate(_FILER_HANDLER, SEAWEEDFS_CLUSTER, _FILER_FAILURES)}"
            f" or 0 * {sum_rate(_FILER_HANDLER, SEAWEEDFS_CLUSTER)}",
            _FILER_HANDLER,
        ),
        description="Failed chunk assignments, uploads, downloads and read streams in filers.",
    ),
)
