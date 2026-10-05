"""Node signals: CPU, memory, filesystems, disks and network (node_exporter)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.metrics.catalog.base import (
    Direction,
    Gate,
    SignalDef,
    aggregate,
    q,
    rate,
    sel,
    sum_rate,
    used_ratio,
)

FS_FILTER = 'fstype=~"ext[234]|xfs|btrfs|zfs", mountpoint!~"/(run|dev|sys|proc)($|/).*"'
DISK_FILTER = 'device!~"(sr|loop|ram|fd)[0-9]+"'
NET_FILTER = 'device!~"lo|veth.*|docker.*|br-.*|virbr.*|cali.*|flannel.*|cni.*"'

NODE = ("job", "instance")
FS = ("job", "instance", "device", "mountpoint")
DEV = ("job", "instance", "device")

_CPU = "node_cpu_seconds_total"

HOST_CATALOG: tuple[SignalDef, ...] = (
    # --- node CPU / memory -----------------------------------------------------------------
    SignalDef(
        "cpu_utilization",
        SignalFamily.CPU,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q("1 - " + aggregate("avg", NODE, rate(_CPU, 'mode="idle"')), _CPU),
        description="Non-idle CPU share (all cores).",
    ),
    SignalDef(
        "cpu_iowait",
        SignalFamily.CPU,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(aggregate("avg", NODE, rate(_CPU, 'mode="iowait"')), _CPU),
        description="CPU share waiting on I/O.",
    ),
    SignalDef(
        "memory_utilization",
        SignalFamily.MEMORY,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            used_ratio("node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes", NODE),
            "node_memory_MemAvailable_bytes",
            "node_memory_MemTotal_bytes",
        ),
        description="1 - MemAvailable/MemTotal.",
    ),
    SignalDef(
        "memory_pressure",
        SignalFamily.MEMORY,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            aggregate("max", NODE, rate("node_pressure_memory_waiting_seconds_total")),
            "node_pressure_memory_waiting_seconds_total",
        ),
        description="PSI: share of time tasks waited on memory.",
    ),
    SignalDef(
        "swap_used_ratio",
        SignalFamily.MEMORY,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            used_ratio("node_memory_SwapFree_bytes", "node_memory_SwapTotal_bytes", NODE),
            "node_memory_SwapFree_bytes",
            "node_memory_SwapTotal_bytes",
        ),
        gates=(
            Gate(
                q(
                    f"count({sel('node_memory_SwapTotal_bytes')} > 0)",
                    "node_memory_SwapTotal_bytes",
                ),
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
            aggregate("max", NODE, rate("node_vmstat_oom_kill", fn="increase")),
            "node_vmstat_oom_kill",
        ),
        description="Kernel OOM kills per step.",
    ),
    SignalDef(
        "io_pressure",
        SignalFamily.DISK_IO,
        Unit.RATIO,
        EntityKind.NODE,
        NODE,
        q(
            aggregate("max", NODE, rate("node_pressure_io_waiting_seconds_total")),
            "node_pressure_io_waiting_seconds_total",
        ),
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
            used_ratio("node_filesystem_avail_bytes", "node_filesystem_size_bytes", FS, FS_FILTER),
            "node_filesystem_avail_bytes",
            "node_filesystem_size_bytes",
        ),
    ),
    SignalDef(
        "filesystem_inodes_used_ratio",
        SignalFamily.FILESYSTEM,
        Unit.RATIO,
        EntityKind.FILESYSTEM,
        FS,
        q(
            used_ratio("node_filesystem_files_free", "node_filesystem_files", FS, FS_FILTER),
            "node_filesystem_files_free",
            "node_filesystem_files",
        ),
    ),
    SignalDef(
        "disk_busy_ratio",
        SignalFamily.DISK_IO,
        Unit.RATIO,
        EntityKind.DISK,
        DEV,
        q(
            aggregate("max", DEV, rate("node_disk_io_time_seconds_total", DISK_FILTER)),
            "node_disk_io_time_seconds_total",
        ),
    ),
    SignalDef(
        "disk_io_bytes",
        SignalFamily.DISK_IO,
        Unit.BYTES_PER_SECOND,
        EntityKind.DISK,
        DEV,
        # Name regexes select several metrics, so the query declares none and the required
        # metrics are listed explicitly.
        q(
            "sum by (job, instance, device) (rate("
            '{{__name__=~"node_disk_(read|written)_bytes_total", {s}, ' + DISK_FILTER + "}}[{w}]))"
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
            sum_rate("node_network_receive_bytes_total", DEV, NET_FILTER),
            "node_network_receive_bytes_total",
        ),
        direction=Direction.BOTH,
    ),
    SignalDef(
        "network_transmit_bytes",
        SignalFamily.NETWORK,
        Unit.BYTES_PER_SECOND,
        EntityKind.NETWORK_INTERFACE,
        DEV,
        q(
            sum_rate("node_network_transmit_bytes_total", DEV, NET_FILTER),
            "node_network_transmit_bytes_total",
        ),
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
)
