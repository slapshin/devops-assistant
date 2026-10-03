# Metrics catalog (T004)

The catalog is `catalog-2026.09.1` in `backend/app/metrics/catalog.py`. It holds one scoped query template per signal and is collected by `PrometheusMetricsSource` (`backend/app/metrics/source.py`) through the bounded client (`backend/app/metrics/client.py`).

## Scope enforcement

Templates write each selector as `metric{{{s}, …}}`. `{s}` expands to the project's equality matchers sorted by label name, e.g. `env="<e>", project="<p>"` (T012: any 1–10 labels, not only project/env), with `\`, `"` and newlines escaped. `QueryTemplate.render` rejects a query when:

- any matcher block lacks **any** of the project's exact matchers (this covers both operands of every ratio and every gate query), or
- a declared metric name appears without a matcher block.

String literals are masked before the check, so label values cannot fake a selector. As a second line of defence, the source drops any returned series whose matcher labels differ from the scope and records this in `exclusions`. Every result series is labelled with the scope's matchers. The project connection test (`app/metrics/probe.py`) uses the same scope rules.

## Semantics

- **Rates before aggregation:** every counter (`*_total`, `*_count`, `*_sum`, `*_bucket`) is wrapped directly in `rate()`/`increase()` before `sum`/`avg`/`max`. Counter resets are therefore handled per series by the source (a test enforces this).
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

The rules are constants in `catalog.py`. Changing them requires a new `CATALOG_VERSION`.

## Signals

| Signal | Family | Unit | Entity | Gate |
| --- | --- | --- | --- | --- |
| cpu_utilization, cpu_iowait | cpu | ratio | node | — |
| memory_utilization, memory_pressure (PSI) | memory | ratio | node | — |
| swap_used_ratio | memory | ratio | node | SwapTotal > 0 |
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
