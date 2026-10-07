# Metrics catalog (T004)

The catalog is `catalog-2026.10.6` in `backend/app/sources/prometheus/catalog/` (one module per source; `__init__.py` assembles `CATALOG`). It holds one scoped query template per signal and is collected by `PrometheusMetricsSource` (`backend/app/sources/prometheus/source.py`) through the bounded client (`backend/app/sources/prometheus/client.py`).

## Scope enforcement

Templates write each selector as `metric{{{s}, …}}`. `{s}` expands to the project's equality matchers sorted by label name, e.g. `env="<e>", project="<p>"` (T012: any 1–10 labels, not only project/env), with `\`, `"` and newlines escaped. `QueryTemplate.render` rejects a query when:

- any matcher block lacks **any** of the project's exact matchers (this covers both operands of every ratio and every gate query), or
- a declared metric name appears without a matcher block.

String literals are masked before the check, so label values cannot fake a selector. As a second line of defence, the source drops any returned series whose matcher labels differ from the scope and records this in `exclusions`. Every result series is labelled with the scope's matchers. The project connection test (`app/sources/prometheus/probe.py`) uses the same scope rules.

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
- **Host labels:** where an exporter can name the served host or a close analog, the label is part of the identity: OTel `server_address`, Caddy `host`, Traefik `router`. These labels are optional. They are not required for support, and when absent they are left out of the entity, so its key and name are unchanged. A route name with a host reads `job · GET /orders @ shop.example.com`.

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
| Caddy | built-in metrics | `job, host, server, handler` (`host` only with `metrics { per_host }`); upstream `job, upstream` | `caddy_requests`, `caddy_5xx`, `caddy_4xx`, `caddy_404` (all from `caddy_http_request_duration_seconds_count{code}`), `caddy_latency_p95/p99`, `caddy_upstream_unhealthy` | buckets present |
| Traefik | built-in Prometheus exporter (service labels, the default; router labels with `addRoutersLabels`) | `job, router, service` (`router` only from router metrics); entrypoint `job, entrypoint`; server `job, service, url` | `traefik_requests`, `traefik_5xx`, `traefik_4xx`, `traefik_404`, `traefik_latency_p95/p99`, `traefik_connections_active` (`traefik_open_connections`), `traefik_server_down` | buckets present |

Notes and limitations:

- nginx stub_status exposes no status codes or latency, so nginx yields no failure ratio or latency signal. NGINX Plus (`nginxplus_*`) and the VTS module are not covered.
- Angie latency is exported only as peer response-time averages, not histograms, so no Angie latency signal is collected (latency requires buckets). Angie `down` (state 2) is operator-configured and is not counted as unavailable.
- Caddy counts each handler in a chain, so the `handler` label is part of the identity rather than summed.
- Traefik has no host label on its histograms. Routers are the closest analog because each one usually matches one `Host()` rule. A service that has router series (`traefik_router_*`) is analysed per router; services without router series fall back to `traefik_service_*`. Presence is tested per service on the unfiltered router counter, so one service never mixes the two. A `headerLabels` host label (via `X-Forwarded-Host`, since Go drops `Host` from the header map) reaches only `requests_total`, not the histograms, so it is not used.
- Angie's server zone (`status_zone`) is already the virtual-host analog. nginx stub_status has no per-host data.
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

### Cloudflare (T015)

A project's Cloudflare source analyses one zone, optionally narrowed to some hostnames (`clientRequestHTTPHost_in`). It uses the GraphQL Analytics API (`app/sources/cloudflare/`, catalog `cloudflare-2026.10.6`). An API token with **Analytics: Read** on the zone is enough. Every query is filtered by zone, time range and hostnames and grouped by `datetimeFiveMinutes`, so one row is one bucket of the analysis grid. HTTP queries keep only end-user traffic (`requestSource: "eyeball"`).

| Signal | Dataset and field | Unit |
| --- | --- | --- |
| `cf_requests` | `httpRequestsAdaptiveGroups.count` (+ `avg.sampleInterval`) | req/s |
| `cf_5xx`, `cf_52x`, `cf_404`, `cf_4xx` | `count` filtered by `edgeResponseStatus` (500–599, 520–530, 404, 400–499) | req/s |
| `cf_cache_hits` | `count` filtered by `cacheStatus_in: [hit, stale, updating, revalidated]` | req/s |
| `cf_ttfb_p95`, `cf_ttfb_p99`, `cf_origin_p95` | `quantiles.edgeTimeToFirstByteMsP95/P99`, `quantiles.originResponseDurationMsP95` (Pro plan and up) | s |
| `cf_blocked`, `cf_challenged` | `firewallEventsAdaptiveGroups.count` by `action` (block/connection close; challenge/JS/managed challenge) | events/s |

- **Discovery**:
  - The `settings` node gives each dataset's `enabled`, `maxDuration`, `notOlderThan` and `maxPageSize`. When it cannot be read, the documented defaults apply (1 day per query, 31 days back) and capabilities are unverified.
  - One-hour probes of the timing quantiles and firewall events mark them `unsupported`, with Cloudflare's message, when the plan or token refuses them.
  - History comes from `httpRequests1dGroups` for the whole zone.
- **Collection**:
  - The 28-day grid is fetched in chunks of at most one day (or the dataset's `maxDuration`, if smaller), never earlier than `notOlderThan`.
  - Within a fetched chunk, buckets without rows are zero for counts and unknown for quantiles.
  - Failed chunks become `query_failed`/`query_timeout` exclusions and stay unknown. A result at the page-size limit adds `series_truncated`.
  - A full analysis makes about 28 × 3 requests, well inside Cloudflare's 300 queries per 5 minutes. A rate-limited request is retried once after `Retry-After`.
- **Live verification pending**: no live zone was available during T015. The field names follow Cloudflare's documentation (see the T015 task file), and a refused field degrades only its signals to `unsupported`.

### Sentry (T017)

A project's Sentry source analyses up to 10 Sentry projects of one organization, each as its own entity (kind `application`), optionally narrowed to one environment and to tag filters that apply to every project. It uses the REST API (`app/sources/sentry/`, catalog `sentry-2026.10.8`): `GET /api/0/projects/{org}/{project}/` per project to check access and read the numeric project ID and creation date, `GET /api/0/organizations/{org}/events/` to rank error kinds, and `GET /api/0/organizations/{org}/events-timeseries/` with `project=<numeric ID>` (Sentry 25.x rejects slugs there), `environment=<name>`, `interval=5m` and several `yAxis` aggregates per request. Tag filters are appended to `query` as quoted terms (`team:"shop"`, quotes and backslashes escaped), all of which must match; built-in fields such as `server_name` or `release` work the same way. An auth token with **org:read** and **project:read** is enough (organization auth tokens for CI cannot read events). EU organizations use `https://de.sentry.io`. Self-hosted Sentry needs `events-timeseries`, which exists from 25.x (checked against the 25.5.1 source: it answers with `timeseries`, intervals in ms, and maps `dataset=spans` to the new span store; all three are handled).

| Signal | Dataset, filter and aggregate | Unit |
| --- | --- | --- |
| `sentry_errors` | `errors`, `count()`; per error kind: `issue:[…]`, `groupBy=issue` | events/s |
| `sentry_error_users` | `errors`, `count_unique(user)` | users per 5 min |
| `sentry_unhandled` | `errors`, `error.unhandled:true`, `count()` | events/s |
| `sentry_transactions` | `spans` + `is_transaction:true`, else `transactions`: `count()` | /s |
| `sentry_transaction_failures` | same dataset, `failure_rate() × count()` | /s |
| `sentry_duration_p95`, `sentry_duration_p99` | `p95/p99(span.duration)` or `p95/p99(transaction.duration)` (ms → s) | s |

- **Discovery**:
  - Reading the projects proves the token and slugs; 401/403/404 fail the source as an auth error. A redirect (e.g. a US URL for an EU organization) is reported with the target.
  - 24-hour probes of each query, over all projects in one request, mark it `unsupported`, with Sentry's message, when it is refused. Transactions are probed in the `spans` dataset first (sentry.io) and then in the classic `transactions` dataset (self-hosted); the first one that is accepted and has transactions is used, and the chosen dataset appears in the capability. Without any transaction in the last 24 h in either, the transaction signals are `unsupported` ("tracing not set up").
  - History is the age of the oldest project (up to 30 days).
- **Collection**:
  - The 28-day grid is fetched per project in chunks of at most seven days (2016 buckets; Sentry allows 10,000 points per request) from the project's creation onwards: 3 queries × 4 chunks per project.
  - **Error kinds**: error events are analysed as each project's total and per Sentry issue. The project's top issues are ranked with `events` (`field=issue,title,count()`, `sort=-count()`): the top 5 of the latest 24 h first (so a new error kind is never crowded out), then the top of the whole 28 days, up to 10 (Sentry's `topEvents` maximum). They are fetched as `events-timeseries` grouped by `issue` and filtered to exactly those issues (`issue:[SHOP-1,SHOP-2]`, `topEvents=<n>`, `excludeOther=1`), so every chunk returns the same groups; 10 series per request allow 3-day chunks (10 requests per project). Each issue becomes its own entity (`<project> · <issue title> (<short ID>)`, label `issue`); the project's total minus those issues becomes `<project> · (other errors)`, unknown wherever either part is. Short IDs other than `[A-Z0-9][A-Z0-9_-]*` are ignored. When Sentry refuses to rank issues, or refuses every grouped request, only the project's total is analysed. Affected users and unhandled errors stay per project.
  - About 22 requests per project per analysis.
  - A project without any transaction in the fetched window gets no transaction series (it is not traced), while the other projects keep theirs.
  - Within a fetched chunk, buckets without a value are zero for counts and unknown for durations; durations of buckets without transactions are dropped.
  - A response with buckets other than 5 minutes is rejected. Failed chunks become `query_failed`/`query_timeout` exclusions and stay unknown. A rate-limited request is retried once after `Retry-After`.
- **Live verification pending**: no live Sentry organization was available during T017. Request and response shapes follow Sentry's API documentation (see the T017 task file).

### Wazuh (T018)

A project's Wazuh source analyses the security alerts of selected Wazuh agents, each agent as its own entity (kind `agent`). It reads the **Wazuh indexer** (OpenSearch API, default port 9200; `app/sources/wazuh/`, catalog `wazuh-2026.10.7`), not the Wazuh server API, which has no alert history. Agents are selected by name (`agent.name`, up to 50), by group (up to 10) and/or by agent labels (`agent.labels.<key> = value`, up to 10, all must match; set in the agent's `<labels>` block or a group's `agent.conf`); at least one is required. The named agents and the groups' members together form the selection, and labels narrow it. Alerts do not carry groups, so a group's members are read from the `wazuh-monitoring-*` index (agent snapshots the Wazuh dashboard writes every 15 minutes when `wazuh.monitoring.enabled` is on; pattern configurable): the agents in that group in any snapshot of the 24 h before T, up to 1,000 per group. A missing monitoring index fails the source with that hint; a group without members gets a `no_data` exclusion and never widens the selection. Every request is `POST /<index pattern>/_search` (default `wazuh-alerts-4.x-*`) with `size: 0` and aggregations, and its query uses only `term`, `terms` and `range` clauses, so configured values can never change the query. Basic auth with an indexer user that may read the alerts indices is enough.

| Signal | Filter | Unit |
| --- | --- | --- |
| `wazuh_alerts` | every alert | alerts/s |
| `wazuh_high_alerts` | `rule.level >= 12` | alerts/s |
| `wazuh_auth_failures` | `rule.groups` ∈ {`authentication_failed`, `authentication_failures`, `invalid_login`} | alerts/s |
| `wazuh_fim_changes` | `rule.groups = syscheck` | changes/s |

- **Discovery**: one search over the 28-day window lists the selected agents with alerts (`terms` on `agent.name`, up to 51) and each agent's first alert. More than 50 agents keeps the first 50 by name and adds a `series_truncated` exclusion (the report is `partial`). Configured agent names without any alert get a `no_data` exclusion. Without any alert, every signal is `unsupported`. History is the age of the oldest first alert.
- **Collection**:
  - One request per time chunk covers all agents: `terms` on `agent.name` → 5-minute `date_histogram` on `timestamp` → a `filters` sub-aggregation with one bucket per filtered signal. Chunks are sized to stay under 40,000 aggregation buckets (OpenSearch's default `search.max_buckets` is 65,535) and at most seven days: 16 hours for 50 agents (42 requests per analysis), seven days for up to 4 agents (4 requests).
  - Inside a fetched chunk a bucket without alerts is zero, from the agent's first alert on; before it values are unknown.
  - A search that timed out, failed on some shards, or matched no index fails its chunk (`query_failed`/`query_timeout`, unknown periods) instead of returning partial counts. 401/403 fail the source as an auth error; overloads (429) and 5xx are retried once.
  - The indexer version is read best-effort from `GET /` (a role limited to the alerts indices may not see it).
- **Live verification pending**: no live Wazuh indexer was available during T018. Field names follow the Wazuh 4.x alerts template (see the T018 task file).

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
