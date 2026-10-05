"""Host rules: CPU, memory, disk I/O, filesystem and network (node exporter)."""

from app.analysis.rules.base import Dir, Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
HOST_RULES = (
    Rule("cpu_utilization", F.CPU, "CPU utilisation", thresholds="cpu_utilization"),
    Rule("cpu_iowait", F.CPU, "CPU I/O wait", thresholds="cpu_iowait"),
    Rule("memory_utilization", F.MEMORY, "Memory utilisation", thresholds="memory_utilization"),
    Rule("memory_pressure", F.MEMORY, "Memory pressure (PSI)", thresholds="memory_pressure"),
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
)
