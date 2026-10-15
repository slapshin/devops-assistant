"""Runtime rules (Node.js via prom-client): event loop, heap, GC, handles, CPU, descriptors."""

from app.analysis.rules.base import Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
RUNTIME_RULES = (
    Rule(
        "runtime_eventloop_lag",
        F.RUNTIME,
        "Event loop lag (p99)",
        thresholds="runtime_eventloop_lag",
        title_up="Event loop lag above expected range",
    ),
    Rule(
        "runtime_heap_used",
        F.RUNTIME,
        "Heap in use",
        thresholds="runtime_heap_used",
        severity_cap=Severity.MEDIUM,
        title_up="Heap use above expected range (possible leak)",
    ),
    Rule(
        "runtime_gc_time_ratio",
        F.RUNTIME,
        "Time in garbage collection",
        thresholds="runtime_gc_time_ratio",
    ),
    Rule(
        "runtime_active_handles",
        F.RUNTIME,
        "Active handles",
        thresholds="runtime_active_handles",
        severity_cap=Severity.MEDIUM,
        title_up="Active handles above expected range (possible handle leak)",
    ),
    Rule("runtime_cpu", F.RUNTIME, "Process CPU", thresholds="runtime_cpu"),
    Rule(
        "runtime_fds_ratio",
        F.RUNTIME,
        "File descriptors vs limit",
        thresholds="runtime_fds_ratio",
    ),
)
