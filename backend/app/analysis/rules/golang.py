"""Go rules (client_golang default Go and process collectors): goroutines, threads, heap, GC
pauses, CPU, descriptors.

Heap, CPU and descriptor thresholds are shared with the Node.js rules.
"""

from app.analysis.rules.base import Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
GO_RULES = (
    Rule(
        "go_goroutines",
        F.GOLANG,
        "Goroutines",
        thresholds="runtime_goroutines",
        severity_cap=Severity.MEDIUM,
        title_up="Goroutines above expected range (possible goroutine leak)",
    ),
    Rule(
        "go_threads",
        F.GOLANG,
        "OS threads",
        thresholds="runtime_threads",
        severity_cap=Severity.MEDIUM,
    ),
    Rule(
        "go_heap_used",
        F.GOLANG,
        "Heap in use",
        thresholds="runtime_heap_used",
        severity_cap=Severity.MEDIUM,
        title_up="Heap use above expected range (possible leak)",
    ),
    Rule(
        "go_gc_pause_ratio",
        F.GOLANG,
        "Time in GC pauses",
        thresholds="runtime_gc_pause_ratio",
    ),
    Rule("go_cpu", F.GOLANG, "Process CPU", thresholds="runtime_cpu"),
    Rule(
        "go_fds_ratio",
        F.GOLANG,
        "File descriptors vs limit",
        thresholds="runtime_fds_ratio",
    ),
)
