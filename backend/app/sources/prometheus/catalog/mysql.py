"""MySQL signals (mysqld-exporter)."""

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

# prom/mysqld-exporter with its default collectors (global_status, global_variables,
# slave_status). `instance` is the exporter target (one MySQL server) and every signal is
# server-wide. SHOW GLOBAL STATUS counters have no conventional suffix.
MYSQL = ("job", "instance")
MYSQL_TRAFFIC = "mysql_queries"

_MYSQL_QUESTIONS = "mysql_global_status_questions"
_MYSQL_SLOW = "mysql_global_status_slow_queries"
_MYSQL_CONN_ERRORS = "mysql_global_status_connection_errors_total"
_MYSQL_ROW_LOCK_WAITS = "mysql_global_status_innodb_row_lock_waits"
_MYSQL_TMP_DISK_TABLES = "mysql_global_status_created_tmp_disk_tables"


def _mysql_replication(variant: str, source: str) -> tuple[SignalDef, SignalDef]:
    """Lag and thread state, named after SHOW SLAVE STATUS or SHOW REPLICA STATUS columns.

    The exporter lower-cases column names: MySQL 8.4 dropped SHOW SLAVE STATUS, so its replicas
    export ``replica_*_running`` and ``seconds_behind_source`` instead.
    """
    lag = f"mysql_slave_status_seconds_behind_{source}"
    sql = f"mysql_slave_status_{variant}_sql_running"
    io = f"mysql_slave_status_{variant}_io_running"
    return (
        SignalDef(
            f"mysql_{variant}_lag",
            SignalFamily.DATABASE,
            Unit.SECONDS,
            EntityKind.DATABASE,
            MYSQL,
            q(aggregate("max", MYSQL, sel(lag)), lag),
            description="Replica lag; absent while the SQL thread is stopped.",
        ),
        SignalDef(
            f"mysql_{variant}_stopped",
            SignalFamily.DATABASE,
            Unit.COUNT,
            EntityKind.DATABASE,
            MYSQL,
            q("1 - " + aggregate("min", MYSQL, f"{sel(sql)} * {sel(io)}"), sql, io),
            description="1 while the SQL or I/O thread is not running (incl. Connecting).",
        ),
    )


MYSQL_CATALOG: tuple[SignalDef, ...] = (
    SignalDef(
        "mysql_down",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        MYSQL,
        q("1 - " + aggregate("max", MYSQL, sel("mysql_up")), "mysql_up"),
        description="1 while the exporter cannot connect to MySQL.",
    ),
    SignalDef(
        "mysql_connections_used_ratio",
        SignalFamily.DATABASE,
        Unit.RATIO,
        EntityKind.DATABASE,
        MYSQL,
        q(
            f"{aggregate('max', MYSQL, sel('mysql_global_status_threads_connected'))}"
            f" / {aggregate('max', MYSQL, sel('mysql_global_variables_max_connections') + ' > 0')}",
            "mysql_global_status_threads_connected",
            "mysql_global_variables_max_connections",
        ),
        description="Open client connections vs max_connections.",
    ),
    SignalDef(
        "mysql_connections_refused",
        SignalFamily.DATABASE,
        Unit.COUNT,
        EntityKind.DATABASE,
        MYSQL,
        q(
            sum_rate(_MYSQL_CONN_ERRORS, MYSQL, 'error="max_connections"', fn="increase"),
            _MYSQL_CONN_ERRORS,
        ),
        description="Connections refused per step because max_connections was reached.",
    ),
    *_mysql_replication("slave", "master"),
    *_mysql_replication("replica", "source"),
    SignalDef(
        MYSQL_TRAFFIC,
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(sum_rate(_MYSQL_QUESTIONS, MYSQL), _MYSQL_QUESTIONS),
        direction=Direction.BOTH,
        description="Statements sent by clients per second (Questions).",
    ),
    SignalDef(
        "mysql_slow_queries",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(sum_rate(_MYSQL_SLOW, MYSQL), _MYSQL_SLOW),
        role=Role.OPERAND,
    ),
    SignalDef(
        "mysql_row_lock_waits",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(sum_rate(_MYSQL_ROW_LOCK_WAITS, MYSQL), _MYSQL_ROW_LOCK_WAITS),
        description="InnoDB row lock waits per second.",
    ),
    SignalDef(
        "mysql_tmp_disk_tables",
        SignalFamily.DATABASE,
        Unit.PER_SECOND,
        EntityKind.DATABASE,
        MYSQL,
        q(sum_rate(_MYSQL_TMP_DISK_TABLES, MYSQL), _MYSQL_TMP_DISK_TABLES),
        description="Internal temporary tables created on disk per second.",
    ),
)
