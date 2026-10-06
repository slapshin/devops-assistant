"""PostgreSQL signals (postgres_exporter)."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.sources.prometheus.catalog.base import (
    Direction,
    Role,
    SignalDef,
    aggregate,
    q,
    sel,
    sum_rate,
)

# prometheus-community/postgres_exporter with its default collectors. `instance` is the exporter
# target (one PostgreSQL server); the pg_stat_database counters have no conventional suffix.
PG_SERVER = ("job", "instance")
PG_DB = ("job", "instance", "datname")
# Template databases never take connections. postgres_exporter skips the shared-objects row
# (no datname), so the empty alternative only guards older versions.
PG_DB_FILTER = 'datname!~"|template[01]"'
PG_TRAFFIC = "pg_transactions"

_PG_COMMIT = "pg_stat_database_xact_commit"
_PG_ROLLBACK = "pg_stat_database_xact_rollback"


def _pg_rate(metric: str, fn: str = "rate") -> str:
    return sum_rate(metric, PG_DB, PG_DB_FILTER, fn=fn)


POSTGRES_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "pg_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        PG_SERVER,
        q("1 - " + aggregate("max", PG_SERVER, sel("pg_up")), "pg_up"),
        description="1 while the exporter cannot connect to PostgreSQL.",
    ),
    SignalDef(
        "pg_connections_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        PG_SERVER,
        q(
            f"{aggregate('sum', PG_SERVER, sel('pg_stat_database_numbackends'))}"
            f" / {aggregate('max', PG_SERVER, sel('pg_settings_max_connections') + ' > 0')}",
            "pg_stat_database_numbackends",
            "pg_settings_max_connections",
        ),
        description="Backends connected to any database vs max_connections.",
    ),
    SignalDef(
        "pg_replication_lag",
        SignalFamily.DATABASE,
        Unit.SECONDS,
        EntityKind.DATABASE,
        PG_SERVER,
        q(
            aggregate("max", PG_SERVER, sel("pg_replication_lag_seconds")),
            "pg_replication_lag_seconds",
        ),
        description="Replay lag on a standby; the exporter reports 0 on a primary.",
    ),
    SignalDef(
        PG_TRAFFIC,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        PG_DB,
        q(f"{_pg_rate(_PG_COMMIT)} + {_pg_rate(_PG_ROLLBACK)}", _PG_COMMIT, _PG_ROLLBACK),
        direction=Direction.BOTH,
        description="Committed plus rolled-back transactions per second.",
    ),
    SignalDef(
        "pg_rollbacks",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        PG_DB,
        q(_pg_rate(_PG_ROLLBACK), _PG_ROLLBACK),
        role=Role.OPERAND,
    ),
    SignalDef(
        "pg_deadlocks",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        PG_DB,
        q(_pg_rate("pg_stat_database_deadlocks", "increase"), "pg_stat_database_deadlocks"),
        description="Deadlocks detected per step.",
    ),
    SignalDef(
        "pg_temp_bytes",
        SignalFamily.DATABASE,
        Unit.BYTES_PER_SECOND,
        EntityKind.DATABASE,
        PG_DB,
        q(_pg_rate("pg_stat_database_temp_bytes"), "pg_stat_database_temp_bytes"),
        description="Temporary file writes (sorts and hashes spilling past work_mem).",
    ),
    SignalDef(
        "pg_longest_transaction",
        SignalFamily.DATABASE,
        Unit.SECONDS,
        EntityKind.DATABASE,
        PG_DB,
        q(
            aggregate("max", PG_DB, sel("pg_stat_activity_max_tx_duration", PG_DB_FILTER)),
            "pg_stat_activity_max_tx_duration",
        ),
        description="Age of the oldest open transaction, including idle in transaction.",
    ),
)
