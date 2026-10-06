"""Redis signals (redis_exporter)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    Direction,
    Gate,
    Role,
    SignalDef,
    aggregate,
    q,
    sel,
    sum_rate,
)

# oliver006/redis_exporter with its defaults (INFO, CONFIG GET maxclients/maxmemory,
# commandstats). `instance` is one Redis server: the exporter address in single-target mode,
# or the target after the usual relabelling in multi-target mode. Every signal is server-wide.
REDIS = ("job", "instance")
REDIS_TRAFFIC = "redis_commands"
REDIS_LOOKUPS = "redis_keyspace_lookups"

_REDIS_HITS = "redis_keyspace_hits_total"
_REDIS_MISSES = "redis_keyspace_misses_total"
_REDIS_CALLS = "redis_commands_total"
_REDIS_CALL_SECONDS = "redis_commands_duration_seconds_total"


def _redis_rate(metric: str, fn: str = "rate") -> str:
    return sum_rate(metric, REDIS, fn=fn)


REDIS_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "redis_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q("1 - " + aggregate("max", REDIS, sel("redis_up")), "redis_up"),
        description="1 while the exporter cannot connect to Redis.",
    ),
    SignalDef(
        "redis_clients_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        REDIS,
        q(
            f"{aggregate('max', REDIS, sel('redis_connected_clients'))}"
            f" / ({aggregate('max', REDIS, sel('redis_max_clients') + ' > 0')}"
            f" or {aggregate('max', REDIS, sel('redis_config_maxclients') + ' > 0')})",
            "redis_connected_clients",
            "redis_max_clients",
            "redis_config_maxclients",
        ),
        # Either limit source will do; the gate checks that one of them is exported.
        ("redis_connected_clients",),
        gates=(
            Gate(
                q(
                    f"count({sel('redis_max_clients')} > 0"
                    f" or {sel('redis_config_maxclients')} > 0)",
                    "redis_max_clients",
                    "redis_config_maxclients",
                ),
                "maxclients is not exported (INFO needs Redis 7+, CONFIG GET may be disabled).",
            ),
        ),
        description="Connected clients vs maxclients (INFO, else CONFIG GET).",
    ),
    SignalDef(
        "redis_rejected_connections",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q(
            _redis_rate("redis_rejected_connections_total", "increase"),
            "redis_rejected_connections_total",
        ),
        description="Connections rejected per step because maxclients was reached.",
    ),
    SignalDef(
        "redis_memory_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        REDIS,
        q(
            f"{aggregate('max', REDIS, sel('redis_memory_used_bytes'))}"
            f" / {aggregate('max', REDIS, sel('redis_memory_max_bytes') + ' > 0')}",
            "redis_memory_used_bytes",
            "redis_memory_max_bytes",
        ),
        gates=(
            Gate(
                q(f"count({sel('redis_memory_max_bytes')} > 0)", "redis_memory_max_bytes"),
                "maxmemory is not set (0) on any Redis server.",
            ),
        ),
        description="used_memory vs maxmemory; servers without maxmemory are skipped.",
    ),
    SignalDef(
        "redis_evictions",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(_redis_rate("redis_evicted_keys_total"), "redis_evicted_keys_total"),
        description="Keys evicted per second under the maxmemory policy.",
    ),
    SignalDef(
        "redis_replica_link_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q("1 - " + aggregate("min", REDIS, sel("redis_master_link_up")), "redis_master_link_up"),
        description="1 while a replica's link to its master is down; absent on masters.",
    ),
    SignalDef(
        "redis_persistence_failed",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        REDIS,
        q(
            "1 - "
            + aggregate(
                "min",
                REDIS,
                f"{sel('redis_rdb_last_bgsave_status')} * {sel('redis_aof_last_write_status')}",
            ),
            "redis_rdb_last_bgsave_status",
            "redis_aof_last_write_status",
        ),
        description="1 while the last RDB snapshot or AOF write failed.",
    ),
    SignalDef(
        REDIS_TRAFFIC,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(_redis_rate("redis_commands_processed_total"), "redis_commands_processed_total"),
        direction=Direction.BOTH,
        description="Commands processed per second.",
    ),
    SignalDef(
        "redis_command_latency_mean",
        SignalFamily.DATABASE,
        Unit.SECONDS,
        EntityKind.DATABASE,
        REDIS,
        q(
            f"{_redis_rate(_REDIS_CALL_SECONDS)} / ({_redis_rate(_REDIS_CALLS)} > 0)",
            _REDIS_CALL_SECONDS,
            _REDIS_CALLS,
        ),
        description="Mean server-side execution time over all commands (commandstats).",
    ),
    SignalDef(
        REDIS_LOOKUPS,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(f"{_redis_rate(_REDIS_HITS)} + {_redis_rate(_REDIS_MISSES)}", _REDIS_HITS, _REDIS_MISSES),
        role=Role.OPERAND,
    ),
    SignalDef(
        "redis_keyspace_misses",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        REDIS,
        q(_redis_rate(_REDIS_MISSES), _REDIS_MISSES),
        role=Role.OPERAND,
    ),
)
