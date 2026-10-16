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
        summary="The event loop was blocked longer, so every request on the process waits.",
        help=(
            "Look for synchronous CPU work (JSON on large payloads, crypto, regex) and "
            "profile the process (--cpu-prof, clinic)."
        ),
    ),
    Rule(
        "nodejs_heap_used",
        F.NODEJS,
        "Heap in use",
        thresholds="runtime_heap_used",
        severity_cap=Severity.MEDIUM,
        title_up="Heap use above expected range (possible leak)",
        summary="The process's heap grew beyond its usual range, which may be a memory leak.",
        help=(
            "Take heap snapshots a few minutes apart and compare retained objects; check "
            "caches and listeners that are never released."
        ),
    ),
    Rule(
        "nodejs_gc_time_ratio",
        F.NODEJS,
        "Time in garbage collection",
        thresholds="runtime_gc_time_ratio",
        summary=(
            "The process spent more time in garbage collection, leaving less time for requests."
        ),
        help=(
            "Usually caused by a heap near its limit or many short-lived allocations; check "
            "heap use and --max-old-space-size."
        ),
    ),
    Rule(
        "nodejs_active_handles",
        F.NODEJS,
        "Active handles",
        thresholds="runtime_active_handles",
        severity_cap=Severity.MEDIUM,
        title_up="Active handles above expected range (possible handle leak)",
        summary=(
            "The process held more active handles (sockets, timers, files), which may be a leak."
        ),
        help=(
            "Look for connections or timers that are never closed, e.g. missing keep-alive "
            "limits or unreleased clients."
        ),
    ),
    Rule(
        "nodejs_cpu",
        F.NODEJS,
        "Process CPU",
        thresholds="runtime_cpu",
        summary="The Node.js process used more CPU than usual.",
        help=(
            "Check whether the load increased, and profile the process for hot code paths "
            "(--cpu-prof)."
        ),
    ),
    Rule(
        "nodejs_fds_ratio",
        F.NODEJS,
        "File descriptors vs limit",
        thresholds="runtime_fds_ratio",
        summary=(
            "The process came closer to its file descriptor limit; at the limit sockets and "
            "files fail to open (EMFILE)."
        ),
        help=(
            "Look for leaked sockets or files (lsof -p) and raise the limit (ulimit -n) if "
            "the usage is legitimate."
        ),
    ),
)
