"""Database rules: shared engine-neutral signals plus PostgreSQL, MySQL and Redis specifics."""

from app.analysis.rules.base import Dir, Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
SHARED_DATABASE_RULES = (
    Rule(
        "database_down",
        F.DATABASE,
        "Database unreachable",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Database unreachable by its exporter",
    ),
    Rule(
        "database_connections_ratio",
        F.DATABASE,
        "Connections vs connection limit",
        thresholds="database_connections_ratio",
    ),
    Rule(
        "database_replication_lag",
        F.DATABASE,
        "Replication lag",
        thresholds="database_replication_lag",
    ),
    Rule(
        "database_connections_refused",
        F.DATABASE,
        "Refused connections",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="Connections refused at the connection limit",
    ),
)

POSTGRES_RULES = (
    Rule(
        "database_transaction_rate",
        F.DATABASE,
        "Transaction rate",
        thresholds="database_transaction_rate",
        direction=Dir.BOTH,
        severity_cap=Severity.MEDIUM,
        title_up="Transaction rate increase",
        title_down="Transaction rate drop",
    ),
    Rule(
        "database_rollback_ratio",
        F.DATABASE,
        "Rolled-back transactions",
        thresholds="database_rollback_ratio",
        volume_guard=True,
        volume_noun="transactions",
        title_up="Rollback share above expected range",
    ),
    Rule(
        "database_deadlocks",
        F.DATABASE,
        "Deadlocks",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="Deadlock detected",
    ),
    Rule(
        "database_temp_bytes",
        F.DATABASE,
        "Temporary file writes",
        thresholds="database_temp_bytes",
        severity_cap=Severity.MEDIUM,
        title_up="Temporary file writes above expected range (work_mem spills)",
    ),
    Rule(
        "database_longest_transaction",
        F.DATABASE,
        "Longest open transaction",
        thresholds="database_longest_transaction",
        title_up="Long-running transaction",
    ),
)

MYSQL_RULES = (
    Rule(
        "database_replication_stopped",
        F.DATABASE,
        "Replication threads",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Replication stopped (SQL or I/O thread not running)",
    ),
    Rule(
        "database_query_rate",
        F.DATABASE,
        "Query rate",
        thresholds="database_query_rate",
        direction=Dir.BOTH,
        severity_cap=Severity.MEDIUM,
        title_up="Query rate increase",
        title_down="Query rate drop",
    ),
    Rule(
        "database_slow_query_ratio",
        F.DATABASE,
        "Slow queries",
        thresholds="database_slow_query_ratio",
        volume_guard=True,
        volume_noun="queries",
        title_up="Slow-query share above expected range",
    ),
    Rule(
        "database_lock_waits",
        F.DATABASE,
        "Row lock waits",
        thresholds="database_lock_waits",
        severity_cap=Severity.MEDIUM,
        title_up="Row lock waits above expected range",
    ),
    Rule(
        "database_tmp_disk_tables",
        F.DATABASE,
        "On-disk temporary tables",
        thresholds="database_tmp_disk_tables",
        severity_cap=Severity.MEDIUM,
        title_up="On-disk temporary tables above expected range",
    ),
)

REDIS_RULES = (
    Rule(
        "database_replica_link_down",
        F.DATABASE,
        "Replica link",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Replica disconnected from its master",
    ),
    Rule(
        "database_persistence_failed",
        F.DATABASE,
        "Persistence",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Persistence failing (last RDB snapshot or AOF write failed)",
    ),
    Rule(
        "database_memory_ratio",
        F.DATABASE,
        "Memory vs maxmemory",
        thresholds="database_memory_ratio",
        title_up="Memory use vs maxmemory above expected range",
    ),
    Rule(
        "database_evictions",
        F.DATABASE,
        "Key evictions",
        thresholds="database_evictions",
        severity_cap=Severity.MEDIUM,
        title_up="Key evictions above expected range",
    ),
    Rule(
        "database_command_rate",
        F.DATABASE,
        "Command rate",
        thresholds="database_query_rate",
        direction=Dir.BOTH,
        severity_cap=Severity.MEDIUM,
        title_up="Command rate increase",
        title_down="Command rate drop",
    ),
    Rule(
        "database_command_latency",
        F.DATABASE,
        "Mean command latency",
        thresholds="database_command_latency",
        volume_guard=True,
        volume_noun="commands",
    ),
    Rule(
        "database_cache_miss_ratio",
        F.DATABASE,
        "Keyspace misses",
        thresholds="database_cache_miss_ratio",
        volume_guard=True,
        volume_noun="lookups",
        severity_cap=Severity.MEDIUM,
        title_up="Keyspace miss share above expected range",
    ),
)

DATABASE_RULES = SHARED_DATABASE_RULES + POSTGRES_RULES + MYSQL_RULES + REDIS_RULES
