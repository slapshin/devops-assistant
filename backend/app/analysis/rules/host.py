"""Host rules: CPU, memory, disk I/O, filesystem and network (node exporter)."""

from app.analysis.rules.base import Dir, Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
HOST_RULES = (
    Rule(
        "cpu_utilization",
        F.CPU,
        "CPU utilisation",
        thresholds="cpu_utilization",
        summary=(
            "The host's CPUs were busier than usual; sustained saturation slows every "
            "process on it."
        ),
        help=(
            "Find the processes using the CPU (top, pidstat) and check for a deploy, cron "
            "job or traffic increase that started with the episode."
        ),
    ),
    Rule(
        "cpu_iowait",
        F.CPU,
        "CPU I/O wait",
        thresholds="cpu_iowait",
        summary=(
            "CPUs spent more time idle waiting for disk I/O, so work on the host is disk-bound."
        ),
        help=(
            "Check the disk busy time and I/O pressure of the same host, and look for "
            "backups, large queries or swapping that read or write heavily (iotop)."
        ),
    ),
    Rule(
        "memory_utilization",
        F.MEMORY,
        "Memory utilisation",
        thresholds="memory_utilization",
        summary=(
            "More of the host's memory was in use; near the limit the kernel swaps or kills "
            "processes."
        ),
        help=(
            "Find the processes with the largest resident memory (ps, smem), compare with "
            "the last restart or deploy, and check for OOM kills on the host."
        ),
    ),
    Rule(
        "memory_pressure",
        F.MEMORY,
        "Memory pressure (PSI)",
        thresholds="memory_pressure",
        summary=(
            "Processes stalled waiting for memory (PSI), so the host is short of memory "
            "even if usage looks moderate."
        ),
        help=(
            "Check for swapping and page-cache thrashing, the largest memory consumers, and "
            "whether container memory limits are too tight."
        ),
    ),
    Rule(
        "node_oom_kills",
        F.MEMORY,
        "Kernel OOM kills",
        kind=RuleKind.EVENT,
        event_points=3,
        title_up="Kernel OOM kill",
        summary="The kernel killed a process because the host ran out of memory.",
        help=(
            "Read the kernel log (dmesg, journalctl -k) for the killed process, then find "
            "what grew in memory before it; consider limits or more memory."
        ),
    ),
    Rule(
        "io_pressure",
        F.DISK_IO,
        "I/O pressure (PSI)",
        thresholds="io_pressure",
        summary=(
            "Processes stalled waiting for I/O (PSI), so disk latency is slowing the host down."
        ),
        help=(
            "Check which devices are busy and which processes drive the I/O (iotop); look "
            "for backups, compaction or a failing disk."
        ),
    ),
    Rule(
        "disk_busy_ratio",
        F.DISK_IO,
        "Disk busy time",
        thresholds="disk_busy_ratio",
        summary="A disk was busy for a larger share of the time; near 100 % requests queue up.",
        help=(
            "Find the processes reading or writing to the device (iotop) and check the "
            "device's latency and health (SMART, cloud volume limits)."
        ),
    ),
    Rule(
        "disk_io_bytes",
        F.DISK_IO,
        "Disk throughput",
        thresholds="disk_io_bytes",
        direction=Dir.BOTH,
        severity_cap=Severity.LOW,
        summary="Disk throughput moved outside its usual range.",
        help=(
            "A rise often comes from backups, batch jobs or swapping; a drop from a stalled "
            "workload. Correlate with the host's other findings."
        ),
    ),
    Rule(
        "filesystem_used_ratio",
        F.FILESYSTEM,
        "Filesystem usage",
        thresholds="filesystem_used_ratio",
        summary="A filesystem filled up more than usual; when it is full, writes fail.",
        help=(
            "Find what grew (du, ncdu), typically logs, temporary files or database data; "
            "clean up, rotate logs or resize the volume."
        ),
    ),
    Rule(
        "filesystem_inodes_used_ratio",
        F.FILESYSTEM,
        "Inode usage",
        thresholds="filesystem_inodes_used_ratio",
        summary=(
            "A filesystem used more inodes; when they run out, files cannot be created even "
            "with free space."
        ),
        help=(
            "Look for directories with many small files (sessions, caches, mail queues) "
            "with find -xdev and remove or consolidate them."
        ),
    ),
    Rule(
        "network_receive_bytes",
        F.NETWORK,
        "Network receive throughput",
        thresholds="network_bytes",
        direction=Dir.BOTH,
        severity_cap=Severity.MEDIUM,
        summary="Inbound network throughput moved outside its usual range.",
        help=(
            "A rise can be a traffic surge, bulk transfer or attack; a drop can mean lost "
            "clients or upstream problems. Compare with request rates."
        ),
    ),
    Rule(
        "network_transmit_bytes",
        F.NETWORK,
        "Network transmit throughput",
        thresholds="network_bytes",
        direction=Dir.BOTH,
        severity_cap=Severity.MEDIUM,
        summary="Outbound network throughput moved outside its usual range.",
        help=(
            "A rise can be large responses, replication or backups leaving the host; a drop "
            "can mean fewer requests served. Compare with request rates."
        ),
    ),
    Rule(
        "network_errors",
        F.NETWORK,
        "Network errors and drops",
        thresholds="network_errors",
        summary="More packets were dropped or failed on a network interface.",
        help=(
            "Check the interface counters (ip -s link), driver and NIC logs, MTU mismatches "
            "and whether receive buffers overflow under load."
        ),
    ),
)
