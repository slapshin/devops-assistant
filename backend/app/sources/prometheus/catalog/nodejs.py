"""Node.js runtime signals (prom-client default metrics)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    SignalDef,
    aggregate,
    q,
    rate,
    sel,
    sum_rate,
)

# prom-client's `collectDefaultMetrics()` without a prefix. `instance` is one scraped process
# (one Node.js process, or a cluster master that aggregates its workers). Generic `process_*`
# metrics are also exported by Go, Python and JVM clients, so those signals keep only the
# targets that export `nodejs_version_info`.
NODEJS = ("job", "instance")

_VERSION_INFO = "nodejs_version_info"
_LAG_P99 = "nodejs_eventloop_lag_p99_seconds"
_HEAP_USED = "nodejs_heap_size_used_bytes"
_GC_SECONDS = "nodejs_gc_duration_seconds_sum"
_HANDLES = "nodejs_active_handles_total"
"""A gauge despite its name."""
_CPU_SECONDS = "process_cpu_seconds_total"
_OPEN_FDS = "process_open_fds"
_MAX_FDS = "process_max_fds"


def _nodejs_only(expr: str) -> str:
    return f"{expr} and on ({', '.join(NODEJS)}) {aggregate('count', NODEJS, sel(_VERSION_INFO))}"


NODEJS_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "nodejs_eventloop_lag",
        SignalFamily.NODEJS,
        Unit.SECONDS,
        EntityKind.PROCESS,
        NODEJS,
        q(aggregate("max", NODEJS, rate(_LAG_P99, fn="max_over_time")), _LAG_P99),
        description="Worst per-scrape p99 event loop delay in the step (perf_hooks histogram, "
        "reset on every scrape).",
    ),
    SignalDef(
        "nodejs_heap_used",
        SignalFamily.NODEJS,
        Unit.BYTES,
        EntityKind.PROCESS,
        NODEJS,
        q(aggregate("sum", NODEJS, rate(_HEAP_USED, fn="avg_over_time")), _HEAP_USED),
        description="V8 heap in use, averaged over the step to smooth the GC sawtooth.",
    ),
    SignalDef(
        "nodejs_gc_time_ratio",
        SignalFamily.NODEJS,
        Unit.RATIO,
        EntityKind.PROCESS,
        NODEJS,
        q(sum_rate(_GC_SECONDS, NODEJS), _GC_SECONDS),
        description="Share of wall-clock time spent in garbage collection (all GC kinds).",
    ),
    SignalDef(
        "nodejs_active_handles",
        SignalFamily.NODEJS,
        Unit.COUNT,
        EntityKind.PROCESS,
        NODEJS,
        q(aggregate("sum", NODEJS, rate(_HANDLES, fn="max_over_time")), _HANDLES),
        description="Open libuv handles (sockets, timers, file watchers); a climb hints at a leak.",
    ),
    SignalDef(
        "nodejs_cpu",
        SignalFamily.NODEJS,
        Unit.PER_SECOND,
        EntityKind.PROCESS,
        NODEJS,
        q(_nodejs_only(sum_rate(_CPU_SECONDS, NODEJS)), _CPU_SECONDS, _VERSION_INFO),
        description="CPU cores used by the process (user + system).",
    ),
    SignalDef(
        "nodejs_fds_used_ratio",
        SignalFamily.NODEJS,
        Unit.RATIO,
        EntityKind.PROCESS,
        NODEJS,
        q(
            _nodejs_only(
                f"{aggregate('sum', NODEJS, sel(_OPEN_FDS))}"
                f" / {aggregate('sum', NODEJS, sel(_MAX_FDS) + ' > 0')}"
            ),
            _OPEN_FDS,
            _MAX_FDS,
            _VERSION_INFO,
        ),
        description="Open file descriptors vs the process limit (Linux only).",
    ),
)
