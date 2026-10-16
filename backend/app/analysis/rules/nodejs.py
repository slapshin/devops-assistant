"""Node.js rules (prom-client default metrics): event loop, heap, GC, handles, CPU, descriptors.

Heap, CPU and descriptor thresholds are shared with the Go rules.
"""

from app.analysis.rules.base import Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
NODEJS_RULES = (
    Rule(
        "nodejs_eventloop_lag",
        F.NODEJS,
        "Event loop lag (p99)",
        thresholds="runtime_eventloop_lag",
        title_up="Event loop lag above expected range",
    ),
    Rule(
        "nodejs_heap_used",
        F.NODEJS,
        "Heap in use",
        thresholds="runtime_heap_used",
        severity_cap=Severity.MEDIUM,
        title_up="Heap use above expected range (possible leak)",
    ),
    Rule(
        "nodejs_gc_time_ratio",
        F.NODEJS,
        "Time in garbage collection",
        thresholds="runtime_gc_time_ratio",
    ),
    Rule(
        "nodejs_active_handles",
        F.NODEJS,
        "Active handles",
        thresholds="runtime_active_handles",
        severity_cap=Severity.MEDIUM,
        title_up="Active handles above expected range (possible handle leak)",
    ),
    Rule("nodejs_cpu", F.NODEJS, "Process CPU", thresholds="runtime_cpu"),
    Rule(
        "nodejs_fds_ratio",
        F.NODEJS,
        "File descriptors vs limit",
        thresholds="runtime_fds_ratio",
    ),
)
