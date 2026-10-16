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
        summary="The process ran more goroutines than usual, which may be a goroutine leak.",
        help=(
            "Take a goroutine profile (pprof /debug/pprof/goroutine) and look for "
            "goroutines blocked on channels or I/O without timeouts."
        ),
    ),
    Rule(
        "go_threads",
        F.GOLANG,
        "OS threads",
        thresholds="runtime_threads",
        severity_cap=Severity.MEDIUM,
        summary="The process created more OS threads than usual.",
        help=(
            "Threads grow with blocking syscalls and cgo calls; check for slow disk or DNS "
            "and take a threadcreate profile."
        ),
    ),
    Rule(
        "go_heap_used",
        F.GOLANG,
        "Heap in use",
        thresholds="runtime_heap_used",
        severity_cap=Severity.MEDIUM,
        title_up="Heap use above expected range (possible leak)",
        summary="The process's heap grew beyond its usual range, which may be a memory leak.",
        help=(
            "Take heap profiles (pprof /debug/pprof/heap) a few minutes apart and compare "
            "inuse_space; check caches and goroutine leaks."
        ),
    ),
    Rule(
        "go_gc_pause_ratio",
        F.GOLANG,
        "Time in GC pauses",
        thresholds="runtime_gc_pause_ratio",
        summary="The process spent more time in GC pauses, which adds latency.",
        help=(
            "Reduce allocations (pprof alloc_space) or tune GOGC and GOMEMLIMIT for the heap size."
        ),
    ),
    Rule(
        "go_cpu",
        F.GOLANG,
        "Process CPU",
        thresholds="runtime_cpu",
        summary="The Go process used more CPU than usual.",
        help=(
            "Check whether the load increased, and take a CPU profile (pprof "
            "/debug/pprof/profile) for hot code paths."
        ),
    ),
    Rule(
        "go_fds_ratio",
        F.GOLANG,
        "File descriptors vs limit",
        thresholds="runtime_fds_ratio",
        summary=(
            "The process came closer to its file descriptor limit; at the limit sockets and "
            "files fail to open."
        ),
        help=(
            "Look for unclosed response bodies, connections or files (lsof -p) and raise "
            "the limit if the usage is legitimate."
        ),
    ),
)
