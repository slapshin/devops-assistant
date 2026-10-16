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
        summary="The exporter could not reach the database, so it may be down.",
        help=(
            "Check that the database runs and accepts connections, then the exporter's "
            "credentials and network path."
        ),
    ),
    Rule(
        "database_connections_ratio",
        F.DATABASE,
        "Connections vs connection limit",
        thresholds="database_connections_ratio",
        summary=(
            "Connections came closer to the database's connection limit; at the limit new "
            "ones are refused."
        ),
        help=(
            "Find the clients holding connections, check for leaked or idle-in-transaction "
            "sessions, and consider a connection pooler."
        ),
    ),
    Rule(
        "database_replication_lag",
        F.DATABASE,
        "Replication lag",
        thresholds="database_replication_lag",
        summary="A replica fell further behind its primary, so reads from it return stale data.",
        help=(
            "Check the replica's I/O and CPU, long-running queries on it, and write bursts "
            "or network problems on the primary."
        ),
    ),
    Rule(
        "database_connections_refused",
        F.DATABASE,
        "Refused connections",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="Connections refused at the connection limit",
        summary="The database refused connections because the connection limit was reached.",
        help=(
            "Find which clients opened the connections, look for leaks or retry storms, and "
            "size the pool against the limit."
        ),
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
        summary="The transaction rate moved outside its usual range.",
        help=(
            "A drop can mean the application stopped writing or cannot connect; a rise new "
            "load or a batch job. Correlate with request rates."
        ),
    ),
    Rule(
        "database_rollback_ratio",
        F.DATABASE,
        "Rolled-back transactions",
        thresholds="database_rollback_ratio",
        volume_guard=True,
        volume_noun="transactions",
        title_up="Rollback share above expected range",
        summary="A larger share of transactions was rolled back.",
        help=(
            "Check application errors and logs for constraint violations, serialization "
            "failures or deadlocks around the episode."
        ),
    ),
    Rule(
        "database_deadlocks",
        F.DATABASE,
        "Deadlocks",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="Deadlock detected",
        summary="Transactions deadlocked and one of them was aborted.",
        help=(
            "Read the database log for the deadlock details and make the involved code "
            "paths lock rows in a consistent order."
        ),
    ),
    Rule(
        "database_temp_bytes",
        F.DATABASE,
        "Temporary file writes",
        thresholds="database_temp_bytes",
        severity_cap=Severity.MEDIUM,
        title_up="Temporary file writes above expected range (work_mem spills)",
        summary=(
            "Queries wrote more temporary files, i.e. sorts or hashes spilled from memory to disk."
        ),
        help=(
            "Find the queries (log_temp_files, pg_stat_statements) and add indexes or raise "
            "work_mem for them."
        ),
    ),
    Rule(
        "database_longest_transaction",
        F.DATABASE,
        "Longest open transaction",
        thresholds="database_longest_transaction",
        title_up="Long-running transaction",
        summary="A transaction stayed open for a long time; it holds locks and blocks vacuum.",
        help=(
            "Find it in pg_stat_activity (xact_start) and check for idle-in-transaction "
            "sessions or a stuck job; set idle_in_transaction_session_timeout."
        ),
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
        summary="Replication stopped: the replica's SQL or I/O thread is not running.",
        help=(
            "Run SHOW REPLICA STATUS for the last error, fix its cause (often a conflicting "
            "row or lost connection) and restart replication."
        ),
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
        summary="The query rate moved outside its usual range.",
        help=(
            "A drop can mean the application cannot connect; a rise new load, a missing "
            "cache or an N+1 pattern after a deploy."
        ),
    ),
    Rule(
        "database_slow_query_ratio",
        F.DATABASE,
        "Slow queries",
        thresholds="database_slow_query_ratio",
        volume_guard=True,
        volume_noun="queries",
        title_up="Slow-query share above expected range",
        summary="A larger share of queries exceeded the slow-query time.",
        help=(
            "Read the slow-query log, check the plans with EXPLAIN, and look for missing "
            "indexes or lock contention."
        ),
    ),
    Rule(
        "database_lock_waits",
        F.DATABASE,
        "Row lock waits",
        thresholds="database_lock_waits",
        severity_cap=Severity.MEDIUM,
        title_up="Row lock waits above expected range",
        summary="Queries waited more often for row locks held by other transactions.",
        help=(
            "Find long transactions and hot rows (performance_schema, SHOW ENGINE INNODB "
            "STATUS) and keep transactions short."
        ),
    ),
    Rule(
        "database_tmp_disk_tables",
        F.DATABASE,
        "On-disk temporary tables",
        thresholds="database_tmp_disk_tables",
        severity_cap=Severity.MEDIUM,
        title_up="On-disk temporary tables above expected range",
        summary="More queries created temporary tables on disk instead of in memory.",
        help=(
            "Find the queries (GROUP BY, ORDER BY, TEXT/BLOB columns) and add indexes or "
            "raise tmp_table_size and max_heap_table_size."
        ),
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
        summary="A Redis replica lost its link to the master, so it serves stale data.",
        help=(
            "Check network connectivity, the master's output buffer limits for replicas and "
            "both instances' logs."
        ),
    ),
    Rule(
        "database_persistence_failed",
        F.DATABASE,
        "Persistence",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Persistence failing (last RDB snapshot or AOF write failed)",
        summary=(
            "Redis failed to persist data (RDB snapshot or AOF write); a restart may lose data."
        ),
        help=(
            "Check the Redis log, free disk space and permissions; failing forks often mean "
            "too little memory (vm.overcommit_memory)."
        ),
    ),
    Rule(
        "database_memory_ratio",
        F.DATABASE,
        "Memory vs maxmemory",
        thresholds="database_memory_ratio",
        title_up="Memory use vs maxmemory above expected range",
        summary=(
            "Redis used more of its maxmemory; at the limit it evicts keys or rejects writes."
        ),
        help=(
            "Find large keys (redis-cli --bigkeys, MEMORY USAGE), check TTLs, and raise "
            "maxmemory or reduce data."
        ),
    ),
    Rule(
        "database_evictions",
        F.DATABASE,
        "Key evictions",
        thresholds="database_evictions",
        severity_cap=Severity.MEDIUM,
        title_up="Key evictions above expected range",
        summary="Redis evicted more keys to stay within maxmemory.",
        help=(
            "Evictions turn into cache misses; raise maxmemory, shorten what is cached, or "
            "check that the eviction policy fits."
        ),
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
        summary="The Redis command rate moved outside its usual range.",
        help=(
            "A drop can mean clients cannot connect; a rise new load, retries or a missing "
            "local cache. Correlate with request rates."
        ),
    ),
    Rule(
        "database_command_latency",
        F.DATABASE,
        "Mean command latency",
        thresholds="database_command_latency",
        volume_guard=True,
        volume_noun="commands",
        summary="Redis commands took longer on average.",
        help=(
            "Check SLOWLOG for expensive commands (KEYS, large ranges), CPU saturation and "
            "persistence forks."
        ),
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
        summary=(
            "A larger share of key lookups missed, so more requests fall through to the "
            "backing store."
        ),
        help=(
            "Check evictions, changed key names or TTLs after a deploy, and a cache flush "
            "or restart."
        ),
    ),
)

DATABASE_RULES = SHARED_DATABASE_RULES + POSTGRES_RULES + MYSQL_RULES + REDIS_RULES
