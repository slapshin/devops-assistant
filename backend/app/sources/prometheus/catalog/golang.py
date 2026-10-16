"""Go runtime signals (client_golang default Go and process collectors)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    SignalDef,
    aggregate,
    q,
    rate,
    sel,
    sum_rate,
)

# client_golang's default registry (`GoCollector` + `ProcessCollector`). `instance` is one scraped
# process. Generic `process_*` metrics are also exported by Node.js, Python and JVM clients, so
# those signals keep only the targets that export `go_info`.
GO = ("job", "instance")

_INFO = "go_info"
_GOROUTINES = "go_goroutines"
_THREADS = "go_threads"
_HEAP_ALLOC = "go_memstats_heap_alloc_bytes"
_GC_PAUSE_SECONDS = "go_gc_duration_seconds_sum"
_CPU_SECONDS = "process_cpu_seconds_total"
_OPEN_FDS = "process_open_fds"
_MAX_FDS = "process_max_fds"


def _go_only(expr: str) -> str:
    return f"{expr} and on ({', '.join(GO)}) {aggregate('count', GO, sel(_INFO))}"


GO_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "go_goroutines",
        SignalFamily.GOLANG,
        Unit.COUNT,
        EntityKind.PROCESS,
        GO,
        q(aggregate("sum", GO, rate(_GOROUTINES, fn="max_over_time")), _GOROUTINES),
        description="Live goroutines; a steady climb hints at a goroutine leak.",
    ),
    SignalDef(
        "go_threads",
        SignalFamily.GOLANG,
        Unit.COUNT,
        EntityKind.PROCESS,
        GO,
        q(aggregate("sum", GO, rate(_THREADS, fn="max_over_time")), _THREADS),
        description="OS threads created by the Go runtime; a climb hints at blocking syscalls "
        "or cgo calls.",
    ),
    SignalDef(
        "go_heap_used",
        SignalFamily.GOLANG,
        Unit.BYTES,
        EntityKind.PROCESS,
        GO,
        q(aggregate("sum", GO, rate(_HEAP_ALLOC, fn="avg_over_time")), _HEAP_ALLOC),
        description="Heap objects allocated and not yet freed, averaged over the step to smooth "
        "the GC sawtooth.",
    ),
    SignalDef(
        "go_gc_pause_ratio",
        SignalFamily.GOLANG,
        Unit.RATIO,
        EntityKind.PROCESS,
        GO,
        q(sum_rate(_GC_PAUSE_SECONDS, GO), _GC_PAUSE_SECONDS),
        description="Share of wall-clock time the program was stopped for GC (stop-the-world "
        "pauses only; concurrent marking is not included).",
    ),
    SignalDef(
        "go_cpu",
        SignalFamily.GOLANG,
        Unit.PER_SECOND,
        EntityKind.PROCESS,
        GO,
        q(_go_only(sum_rate(_CPU_SECONDS, GO)), _CPU_SECONDS, _INFO),
        description="CPU cores used by the process (user + system).",
    ),
    SignalDef(
        "go_fds_used_ratio",
        SignalFamily.GOLANG,
        Unit.RATIO,
        EntityKind.PROCESS,
        GO,
        q(
            _go_only(
                f"{aggregate('sum', GO, sel(_OPEN_FDS))}"
                f" / {aggregate('sum', GO, sel(_MAX_FDS) + ' > 0')}"
            ),
            _OPEN_FDS,
            _MAX_FDS,
            _INFO,
        ),
        description="Open file descriptors vs the process limit (Linux only).",
    ),
)
