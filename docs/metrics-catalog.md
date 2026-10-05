# Metrics catalog (T004)

The catalog is `catalog-2026.10.5` in `backend/app/metrics/catalog/` (one module per source; `__init__.py` assembles `CATALOG`). It holds one scoped query template per signal and is collected by `PrometheusMetricsSource` (`backend/app/metrics/source.py`) through the bounded client (`backend/app/metrics/client.py`).

## Scope enforcement

Templates write each selector as `metric{{{s}, …}}`. `{s}` expands to the project's equality matchers sorted by label name, e.g. `env="<e>", project="<p>"` (T012: any 1–10 labels, not only project/env), with `\`, `"` and newlines escaped. `QueryTemplate.render` rejects a query when:

- any matcher block lacks **any** of the project's exact matchers (this covers both operands of every ratio and every gate query), or
- a declared metric name appears without a matcher block.

String literals are masked before the check, so label values cannot fake a selector. As a second line of defence, the source drops any returned series whose matcher labels differ from the scope and records this in `exclusions`. Every result series is labelled with the scope's matchers. The project connection test (`app/metrics/probe.py`) uses the same scope rules.

## Semantics

- **Rates before aggregation:** every counter (`*_total`, `*_count`, `*_sum`, `*_bucket`, plus the unsuffixed proxy counters `angie_http_server_zones_responses`, `angie_connections_dropped`, `nginx_connections_accepted`/`_handled`) is wrapped directly in `rate()`/`increase()` before `sum`/`avg`/`max`. Counter resets are therefore handled per series by the source (a test enforces this).
- **Window and grid:** the rate window is 5 m (scrape interval 10 s, per T003). Collection covers 28 days on a 300 s grid. `values[i]` is the value evaluated at `start + (i+1)·300 s`, meaning the interval `[start + i·300 s, start + (i+1)·300 s)`. Trend buckets therefore contain whole intervals. `null` is a gap.
- **Ratios:** node ratios such as memory and filesystem put `> 0` on the denominator, so a zero denominator is a gap rather than infinity. HTTP/RPC failure ratios are **not** computed in PromQL. `http_5xx`, `http_4xx`, `http_404` and `rpc_errors` are returned beside `http_requests`/`rpc_requests` with the same identity. The analysis treats a missing numerator as 0 **only where the denominator was observed**, and applies the minimum-volume rule.
- **Histograms:** p95/p99 use `histogram_quantile(φ, sum by (…, le) (rate(bucket)))`, gated on bucket presence. With buckets up to 10 s, a quantile at the top bound means "≥ 10 s". Mean latency (`_sum/_count`) is collected only when buckets are absent, and is always labelled as mean.
- **Restarts:** cAdvisor has no trustworthy restart counter, and `container_start_time_seconds` is not used as one. Restart evidence comes from Swarm:
  - `swarm_failed_tasks`: failed/rejected tasks listed by Swarm. Only increases are meaningful, because task history is pruned.
  - `swarm_replica_shortfall`: desired minus running replicas for `mode="replicated"` services.

  Both are proxies. They show task replacement and shortfall, not in-place process restarts.
- **Container identity:** the Swarm task name `<service>.<slot|node>.<25-char task id>` is reduced to `<service>.<slot|node>` with `label_replace`, so an identity survives task replacement. Unnamed cgroups (`name=""`, e.g. `/` and system slices) are excluded to avoid duplicating host totals.
- **Route cardinality:** routes are ranked per service by volume over the last 14 days. The top 20 are kept individually. Rate signals for the rest are summed into `(other routes)`, and quantiles for those routes are dropped (quantiles cannot be summed).

## Exclusion rules

| Rule | Value |
| --- | --- |
| Filesystems | `fstype=~"ext[234]\|xfs\|btrfs\|zfs"`, `mountpoint!~"/(run\|dev\|sys\|proc)($\|/).*"` (so tmpfs, nfs4, overlay are excluded) |
| Disks | `device!~"(sr\|loop\|ram\|fd)[0-9]+"` |
| Network | `device!~"lo\|veth.*\|docker.*\|br-.*\|virbr.*\|cali.*\|flannel.*\|cni.*"` |
| Containers | `name!=""` |

The rules are constants in the `catalog/` source modules (`host.py`, `container.py`). Changing them requires a new `CATALOG_VERSION`.

## Signals

| Signal | Family | Unit | Entity | Gate |
| --- | --- | --- | --- | --- |
| cpu_utilization, cpu_iowait | cpu | ratio | node | — |
| memory_utilization, memory_pressure (PSI) | memory | ratio | node | — |
| node_oom_kills | memory | count/step | node | — |
| io_pressure (PSI), disk_busy_ratio, disk_io_bytes | disk_io | ratio, B/s | node, disk | — |
| filesystem_used_ratio, filesystem_inodes_used_ratio | filesystem | ratio | filesystem | — |
| network_receive_bytes, network_transmit_bytes, network_errors | network | B/s, /s | interface | — |
| container_cpu, container_memory_working_set, container_oom | container | cores, B, count | container | partial when a host has no named containers |
| container_memory_limit_ratio | container | ratio | container | any limit > 0 |
| container_throttling_ratio | container | ratio | container | CFS metrics present |
| swarm_failed_tasks, swarm_replica_shortfall | container | count | swarm service | — |
| http_requests, http_4xx, http_404, http_5xx (operand) | request_* / client_errors | req/s | route | — |
| http_latency_p95, http_latency_p99 | latency | s | route | buckets present |
| http_latency_mean | latency | s | route | fallback only |
| rpc_requests, rpc_errors (operand, status ≠ OK), rpc_latency_p95 | request_* / latency | req/s, s | RPC method | buckets present |

Swap usage is deliberately not collected. A filled swap is normal operation (the kernel moves cold pages out), so it says nothing on its own. Real memory distress shows up in `memory_pressure` (PSI) and `node_oom_kills`.

### Reverse proxies

All proxy series are kind `proxy` (or `upstream` for backend servers) and carry the proxy's scrape `job` in their identity. Status operands are collected beside the request rate with the same identity, like `http_5xx`. Proxies with route-level request signals rank their zones/services per `job` by their own request signal and keep the top 20.

| Proxy | Exporter | Entity (identity) | Signals | Gate |
| --- | --- | --- | --- | --- |
| nginx | nginx-prometheus-exporter (stub_status) | `job, instance` | `nginx_requests`, `nginx_connections_active`, `nginx_connections_dropped` (accepted − handled), `nginx_down` (`1 − nginx_up`) | — |
| Angie | built-in `prometheus` module, stock `prometheus_all.conf` | server zone `job, zone`; process `job, instance`; peer `job, upstream, peer` | `angie_requests`, `angie_5xx`, `angie_4xx`, `angie_404` (from `angie_http_server_zones_responses{code}`), `angie_connections_active`, `angie_connections_dropped`, `angie_peer_unavailable` (state 3 unavailable or 5 unhealthy) | — |
| Caddy | built-in metrics | `job, server, handler`; upstream `job, upstream` | `caddy_requests`, `caddy_5xx`, `caddy_4xx`, `caddy_404` (all from `caddy_http_request_duration_seconds_count{code}`), `caddy_latency_p95/p99`, `caddy_upstream_unhealthy` | buckets present |
| Traefik | built-in Prometheus exporter (service labels, the default) | service `job, service`; entrypoint `job, entrypoint`; server `job, service, url` | `traefik_requests`, `traefik_5xx`, `traefik_4xx`, `traefik_404`, `traefik_latency_p95/p99`, `traefik_connections_active` (`traefik_open_connections`), `traefik_server_down` | buckets present |

Notes and limitations:

- nginx stub_status exposes no status codes or latency, so nginx yields no failure ratio or latency signal. NGINX Plus (`nginxplus_*`) and the VTS module are not covered.
- Angie latency is exported only as peer response-time averages, not histograms, so no Angie latency signal is collected (latency requires buckets). Angie `down` (state 2) is operator-configured and is not counted as unavailable.
- Caddy counts each handler in a chain, so the `handler` label is part of the identity rather than summed.
- Caddy and Traefik upstream/server state uses `min` across proxy instances: one instance seeing a backend down is enough.
- The "≥ 10 s" top-bucket annotation assumes the OTel bucket layout. Traefik's default top bucket is 5 s, so a Traefik p95 at 5 s is also only a lower bound.

### PostgreSQL

Source: [prometheus-community/postgres_exporter](https://github.com/prometheus-community/postgres_exporter) with its default collectors. All series are kind `database`. `instance` is the exporter target, i.e. one PostgreSQL server; in multi-target mode, relabel `instance` to the target. Per-database signals exclude `template0`/`template1` (`datname!~"|template[01]"`). Databases are not ranked or capped beyond the per-query series budget.

| Entity (identity) | Signal | Source metric(s) |
| --- | --- | --- |
| server `job, instance` | `pg_down` | `1 − pg_up` |
| server | `pg_connections_used_ratio` | `Σ pg_stat_database_numbackends / pg_settings_max_connections` |
| server | `pg_replication_lag` | `pg_replication_lag_seconds` (0 on a primary, or when the standby has replayed everything it received) |
| database `job, instance, datname` | `pg_transactions` | `rate(xact_commit) + rate(xact_rollback)` |
| database | `pg_rollbacks` (operand) | `rate(pg_stat_database_xact_rollback)` |
| database | `pg_deadlocks` | `increase(pg_stat_database_deadlocks)` per step |
| database | `pg_temp_bytes` | `rate(pg_stat_database_temp_bytes)` |
| database | `pg_longest_transaction` | `max(pg_stat_activity_max_tx_duration)` over states and users |

Notes and limitations:

- `numbackends` counts backends connected to a database (client sessions and autovacuum workers), not walsenders or other background processes, so the connection ratio is slightly below what PostgreSQL counts against `max_connections`.
- Not covered: lock waits (`pg_locks_count` counts granted locks too), cache hit ratio, table bloat and transaction-ID wraparound (non-default collectors), and query latency (`pg_stat_statements` is not enabled by default).

### MySQL

Source: [prometheus/mysqld_exporter](https://github.com/prometheus/mysqld_exporter) with its default collectors (`global_status`, `global_variables`, `slave_status`). All series are kind `database` with identity `job, instance`; `instance` is the exporter target, i.e. one MySQL server (in multi-target mode, relabel `instance` to the target). Every signal is server-wide: SHOW GLOBAL STATUS has no per-schema breakdown.

| Signal | Source metric(s) |
| --- | --- |
| `mysql_down` | `1 − mysql_up` |
| `mysql_connections_used_ratio` | `mysql_global_status_threads_connected / mysql_global_variables_max_connections` |
| `mysql_connections_refused` | `increase(mysql_global_status_connection_errors_total{error="max_connections"})` per step |
| `mysql_slave_lag` / `mysql_replica_lag` | `mysql_slave_status_seconds_behind_master` / `…_seconds_behind_source` |
| `mysql_slave_stopped` / `mysql_replica_stopped` | `1 − min(…_slave_sql_running × …_slave_io_running)` / the `replica_*` equivalents |
| `mysql_queries` | `rate(mysql_global_status_questions)` |
| `mysql_slow_queries` (operand) | `rate(mysql_global_status_slow_queries)` |
| `mysql_row_lock_waits` | `rate(mysql_global_status_innodb_row_lock_waits)` |
| `mysql_tmp_disk_tables` | `rate(mysql_global_status_created_tmp_disk_tables)` |

Notes and limitations:

- The exporter names replication metrics after the status columns. MySQL up to 8.0 and MariaDB answer `SHOW SLAVE STATUS` (`*_master`, `slave_*`); MySQL 8.4 only answers `SHOW REPLICA STATUS` (`*_source`, `replica_*`). Both variants map onto the same rules, and a server exports only one of them. Primaries export neither.
- `Seconds_Behind_Master` is NULL while the SQL thread is stopped, and the exporter then skips it. The stopped-thread signal covers that case, so a broken replica is never reported as lag-free.
- `Slow_queries` counts statements over `long_query_time` (default 10 s) whether or not the slow log is enabled.
- Not covered: deadlocks (`Innodb_deadlocks` is MariaDB/Percona only; MySQL has it in `innodb_metrics`, a non-default collector), the longest transaction (`info_schema.innodb_trx`), per-schema workload, and statement latency (`perf_schema` collectors are off by default).

### Redis

Source: [oliver006/redis_exporter](https://github.com/oliver006/redis_exporter) with its defaults. All series are kind `database` with identity `job, instance`; `instance` is one Redis server — the exporter address in single-target mode, or the `redis://…` target after the usual relabelling in multi-target mode (`/scrape?target=…`). Every signal is server-wide; per-db keyspace metrics are not used.

| Signal | Source metric(s) |
| --- | --- |
| `redis_down` | `1 − redis_up` |
| `redis_clients_used_ratio` | `redis_connected_clients / (redis_max_clients or redis_config_maxclients)`; gated on either limit being exported |
| `redis_rejected_connections` | `increase(redis_rejected_connections_total)` per step |
| `redis_memory_used_ratio` | `redis_memory_used_bytes / redis_memory_max_bytes`; gated on `maxmemory > 0` |
| `redis_evictions` | `rate(redis_evicted_keys_total)` |
| `redis_replica_link_down` | `1 − min(redis_master_link_up)` (replicas only) |
| `redis_persistence_failed` | `1 − min(redis_rdb_last_bgsave_status × redis_aof_last_write_status)` |
| `redis_commands` | `rate(redis_commands_processed_total)` |
| `redis_command_latency_mean` | `Σ rate(redis_commands_duration_seconds_total) / Σ rate(redis_commands_total)` over all `cmd` |
| `redis_keyspace_lookups` (operand) | `rate(redis_keyspace_hits_total) + rate(redis_keyspace_misses_total)` |
| `redis_keyspace_misses` (operand) | `rate(redis_keyspace_misses_total)` |

Notes and limitations:

- `maxclients` comes from INFO (Redis 7+, `redis_max_clients`) or, on older servers, from `CONFIG GET` (`redis_config_maxclients`); managed services that disable `CONFIG` and run Redis < 7 get no client ratio. `redis_memory_max_bytes` is 0 when `maxmemory` is unset, so the memory ratio exists only for capped servers.
- An `allkeys-*` eviction policy keeps a cache at `maxmemory` by design, so memory has no absolute heuristic; evictions and the miss share show whether the cap hurts.
- Command latency is server-side execution time only (no network, no time blocked in `BLPOP` and friends), averaged over all commands; a burst of slow commands (`KEYS`, large `SMEMBERS`) moves it. `CONFIG RESETSTAT` resets the counters, which `rate` handles.
- The RDB/AOF status stays `err` until the next successful save or write, so a failing disk is reported for as long as it fails. With the default `stop-writes-on-bgsave-error yes`, the master also rejects writes meanwhile.
- Not covered: replication offset lag (redis_exporter exports per-replica offsets on the master, not a lag in seconds), blocked clients (normal for queue consumers), Cluster and Sentinel state, slowlog, and per-command latency.

## Budgets and behaviour

| Aspect | Behaviour |
| --- | --- |
| Timeouts | 30 s per request (httpx); source timeouts are classified `timeout` and not retried |
| Retries | 1 retry for transport errors and 502/503/504/5xx; none for bad queries, timeouts, or auth failures |
| Concurrency | 4 in-flight requests per client |
| Chunking | Range queries are split into ≤ 7-day chunks; boundaries are contiguous and non-overlapping, and results are merged per label set |
| Response size | 64 MiB cap per response (`too_large`) |
| Series | 500 per query and 5,000 per job; the excess is disclosed as `series_truncated` |
| Cache | In-process LRU of 256 entries with a 10 min TTL, keyed by source URL, exact rendered query (which includes the scope), time range, and step |
| Cancellation | Checked before every signal query; asyncio cancellation aborts an in-flight request |
| Failures | A failing signal becomes an `Exclusion` (`query_failed`/`query_timeout`) and collection continues. The job decides `partial`. |

## Service-to-host mappings

At `T`, `collect` returns `mappings` built as follows:

- **OTel services:** `target_info{job, service_instance_id}` → cAdvisor `container_start_time_seconds{id="/system.slice/docker-<id>.scope"}` (12-hex prefix match) → cAdvisor `instance`.
- **Swarm services:** `docker_swarm_task_info{state="running"}` → `node_hostname`.

These are instant snapshots valid at `T`. The analysis uses them only for latest-day grouping.

## Live smoke (2026-09-30)

Run against paas/production through the tunnel, read-only:

- Discovery found 31 projects and envs `development, production` for `paas`.
- Capabilities took 1.2 s and match the T003 inventory: swap, container memory limit, and throttling are unsupported; containers are partial because `paas-production-4` has no named containers.
- Collection took 42.6 s with 126 requests: 27 signals, 360 series, each 8,064 points, with no exclusions.
- Mappings: 5 OTel services → `paas-production-2`, plus the Swarm service placements.
