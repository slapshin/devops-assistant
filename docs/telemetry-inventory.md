# Telemetry inventory (T003)

Discovery ran on 2026-09-30 between ≈ 09:35 and 09:50 UTC against `METRICS_URL=http://localhost:8428`, reached through an SSH tunnel to the monitoring host. Every request was a read-only GET: `/`, `/metrics`, `/api/v1/status/buildinfo`, `/api/v1/status/tsdb?topN=10`, `/api/v1/label/*/values`, `/api/v1/series` (≤ 3,000 results, 10–15 min windows), `/api/v1/query`, and `/api/v1/query_range` (≤ 30 days). No write, admin, delete, snapshot, or export endpoints were called. There were about 40 queries, and each finished in under 1 s except the one cost probe noted in §7.

Status legend:
- **verified**: observed in the live source during this run.
- **absent**: looked for with a scoped query and not found.
- **unverified**: not checked.

The deep inspection covered `project="paas", env="production"`. Other scopes were checked only for pairs, cardinality, and query cost.

## 1. Source and compatibility

| Item | Result | Status |
| --- | --- | --- |
| Backend | Single-node VictoriaMetrics **v1.123.0** (`vm_app_version`); Prometheus-compatible `buildinfo` reports `2.24.0` | verified |
| API | `/api/v1/query`, `query_range`, `series`, `labels`, `label/<n>/values` all work at the root path (no prefix) | verified |
| Authentication | None required on the tunnelled endpoint. The tunnel itself is the access control, and production access beyond it is not assessed. | verified (local tunnel only) |
| Retention | `-retentionPeriod=4w` (flag). Oldest data for paas/production is ≈ **2026-09-01 23:00 UTC**, ≈ 28.4 days before the run. | verified |
| Relevant limits | `search.maxQueryDuration=30s`, `search.maxPointsPerTimeseries=30000`, `search.maxSeries=30000` (series API), `search.latencyOffset=30s`, `dedup.minScrapeInterval=0s` | verified |
| Size | 673,380 series in total; 31 projects | verified |
| Native / VictoriaMetrics `vmrange` histograms | none (`{vmrange!=""}` is empty); classic `_bucket` histograms only | verified |

## 2. Project/env discovery

`label/project/values` over 28 days returns 31 projects. `count by (project, env) (up)` gives 34 pairs, most of them `production`. There are also `development` envs (`catalog`, `girl`, `paas`, `pw`) and one unusual env name, `paas-gpu-cache/crnv`. `project` and `env` labels are present on ≥ 99.8 % of all series (TSDB label stats), so scope enforcement by label is viable.

The first-release selector design works: projects come from label values, and envs come from `label/env/values?match[]={project="<p>"}`.

## 3. paas/production targets and cadence

| Job | Targets | Series | Notes |
| --- | --- | --- | --- |
| `node` | 4 (`paas-production`, `-2`, `-4`, `-storage`) | 4,236 | node exporter |
| `cadvisor` | 4 (same instance names as node) | 7,974 | cAdvisor v0.49.1 |
| `dispatcher-api` | 3 (`10.0.4.x:5555`) | 3,083 | OTel HTTP server |
| `dispatcher-workerhub-api` | 1 | 215 | OTel HTTP server |
| `dispatcher-workerhub-grpc` | 8 | 1,808 | OTel RPC server |
| `dispatcher-metrics`, `dispatcher-worker` | 1 + 1 | 549 + 56 | OTel, no HTTP/RPC server metrics |
| `paas` | 1 | 1,425 | Docker Swarm exporter (`docker_swarm_*`) |
| `traefik` | 1 | 1,800 | Edge proxy HTTP metrics |
| others | kafka, redis ×2, seaweedfs ×4 roles, balances | — | Out of first-release scope |

- **Scrape interval: 10 s for every job** (`scrape_interval(up[1h])` median). A 5-minute window holds 30 samples.
- **Coverage over 28 days:** hourly counts are complete for all 17 jobs (683/683 hours). The 5-minute node CPU series has 8,065/8,064 points with **no gap longer than 5 min**.

## 4. Capabilities (paas/production)

| Family | Metrics observed | Identity labels | Status | Notes |
| --- | --- | --- | --- | --- |
| CPU | `node_cpu_seconds_total` (modes idle, iowait, irq, nice, softirq, steal, system, user) | job, instance, cpu, mode | **supported** (verified) | |
| Memory | `node_memory_MemAvailable_bytes`, `MemTotal_bytes` | job, instance | **supported** | |
| Filesystem | `node_filesystem_{avail,size}_bytes`, `files`, `files_free`, `readonly` | instance, device, mountpoint, fstype | **supported** | Real filesystems are `ext4` `/dev/sda1` `/` on all four nodes. `nfs4` and `tmpfs` also appear and are excluded by default. `paas-production-2` `/` was **91 % used** during discovery. |
| Disk I/O | `node_disk_io_time_seconds_total`, `read/written_bytes_total` | instance, device | **supported** | Devices `sda`, plus `sr0` (optical, excluded) |
| Network | `node_network_{receive,transmit}_{bytes,errs,drop}_total` | instance, device | **supported** | Devices `eth0`, `eth1`, and `lo` (excluded) |
| Container resources | `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`, `container_network_*`, `container_fs_*` | instance (host), `name` (swarm task), `id` (cgroup), `image`, `container_label_ai_artworks_swarm_component_name` | **partial** | 47 named containers on 3 hosts. `paas-production-4` exposes only its root cgroup (no named containers). |
| Container memory vs limit | `container_spec_memory_limit_bytes` present but **0 for every container** | — | **unsupported** | No limits are configured, so the "≥ 90 % of limit" heuristic cannot apply. |
| CPU throttling | no `container_cpu_cfs_*` metrics | — | **absent → unsupported** | |
| OOM | `container_oom_events_total` (0 increases in 27 d) | name | **supported** | |
| Restarts | `container_start_time_seconds` (cAdvisor); `docker_swarm_task_*`, `docker_swarm_service_replicas_{desired,running}` (swarm exporter, job `paas`) | swarm `service_name`, `task_id`, `node_hostname` | **supported via swarm** | Swarm tasks carry `node_hostname` equal to the node `instance`. Exit-code series include historical completed tasks, so detect *changes*, not the current state. |
| HTTP traffic / status | `http_server_request_duration_seconds_count` | job, instance, `http_route`, `http_request_method`, `http_response_status_code`, `error_type`, `url_scheme`, `otel_scope_name` | **supported** | 58 series across 2 jobs, 16 routes, statuses 200/201/400/401/404 (and 500 historically) |
| HTTP 5xx | same metric | | **supported, extremely rare** | Only 2 × `500` in 27 days, compared with ≈ 42.4 M 2xx responses. The ≥ 30-requests-per-step ratio guard matters here. |
| HTTP 4xx | same metric | | **supported** | 404: 343,357 · 400: 17,955 · 401: 935 in 27 days |
| HTTP latency | `http_server_request_duration_seconds_{bucket,sum,count}` | + `le` | **supported** | Classic buckets 0.005–10 s (+Inf). p95/p99 above 10 s are not resolvable (quantile clamps). |
| RPC traffic / status | `rpc_server_call_duration_seconds_count` | job, instance, `rpc_method`, `rpc_response_status_code`, `rpc_system_name` | **supported** | 10 methods. Statuses: `OK` ≈ 257 M and `UNKNOWN` 1 in 27 days. Failure = non-`OK`. |
| RPC latency | `rpc_server_call_duration_seconds_bucket` | + `le` | **supported** | Same buckets as HTTP |
| Edge HTTP (Traefik) | `traefik_{entrypoint,router,service}_requests_total`, `_request_duration_seconds_bucket` | `code`, `service`, `router`, … | **present, not in first-release catalog** | The `code` label mixes 3-digit codes with single-digit values (`2`, `4`), so treat it as unreliable until reviewed (T004 decision). |

Other examined scopes were not deep-inspected. Their capabilities remain **unverified** and must be discovered per scope at runtime.

## 5. Identity and cross-layer mappings

- **Node:** `(job="node", instance)`, where `instance` is a host alias (`paas-production`). `node_uname_info.nodename` is the container hostname of the exporter (`64be7ccf7e44`), not the host alias, so it must not be used for mapping.
- **Container → host:** cAdvisor `instance` equals the node exporter `instance` (same four names). **Verified mapping.**
- **Service instance → container → host:** the OTel `target_info{job, instance="10.0.4.x:5555"}` has `service_instance_id` (a 12-hex Docker short ID). That ID is the prefix of the Docker ID inside cAdvisor `id="/system.slice/docker-<64hex>.scope"`, and that cAdvisor series has `instance=<host>`. Three of three sampled `dispatcher-api` instances resolved this way (e.g. `10.0.4.251:5555` → `paas-production-2`). **Verified mapping by labels.** It changes on every redeploy, so it must be joined per time step, never cached across the window.
- **Swarm service → host:** `docker_swarm_task_info{service_name, node_hostname}` is a second label-based mapping.
- The owner-supplied note that `paas-production` and `10.0.4.251:5555` "are different namespaces" still holds: no relationship follows from the names alone. The mapping above comes from shared label values, which the architecture allows. For the sampled instance, the host was `paas-production-2`, not `paas-production`.

## 6. Observed data quirks

- `error_type` is **not** a reliable failure classifier. It mirrors the status code, including 5 requests with `error_type="200"` and status `200`. Classify by `http_response_status_code` only (already in DECISIONS §5).
- No 5xx and only one non-OK RPC status in 27 days means relative 5xx/RPC-failure baselines are almost always zero. The robust-scale floor and the absolute ratio check (≥ 5 % with ≥ 30 requests) are what make failures detectable at all.
- Traefik `code` values `2` and `4` (see §4).
- `/internal/healthcheck` and similar internal routes are in the HTTP series. T004 should keep them but let the per-service top-20 rule decide route-level analysis.

## 7. Query cost and cardinality

| Probe | Result |
| --- | --- |
| paas/production, 28 d + 5 min, 300 s step, `1 - avg by (job, instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))` | 4 series, 32,264 points, **1.7 s** |
| paas/production, 28 d HTTP p95 by route | 22 series, **1.4 s** |
| **paas-gpu/production**, same CPU query | 48 series, 386,538 points, **22.6 s** (limit 30 s) |
| Largest scopes by selected-metric series | paas-gpu 23,246 · minio 3,220 · paas 2,480 · pw-com 1,772 |

A full 28-day range per query is too close to the 30 s server limit for large scopes. See the budget update in DECISIONS §6.

## 8. Consequences for downstream tasks

- **DECISIONS §5 and §6** are updated accordingly: scrape interval 10 s, so the rate window stays at `max(300 s, 40 s) = 300 s`. Collection must split range queries into ≤ 7-day chunks. Collection starts at `T-28d-5m`, the edge of 4-week retention, so the oldest trend bucket's first baseline day can be partially covered; the coverage rules handle this.
- **T004:** implement the catalog for the verified families above, with container throttling and memory-vs-limit marked unsupported. Restart detection comes from swarm task state changes and `container_start_time_seconds` changes. RPC failure means `rpc_response_status_code!="OK"`. Service-to-host mapping is joined per step as in §5. Excluded devices: `lo`, `sr0`, and fstypes `tmpfs`, `nfs4`, `overlay`.
- **T005:** the 5xx and RPC failure baselines are near zero, so rely on absolute checks with volume guards. `paas-production-2` `/` at 91 % will trigger the provisional filesystem heuristic, a good live check for T010.
- **T010:** re-run live validation on paas/production and at least one large scope (paas-gpu) for query budgets. Scopes other than paas/production remain unverified.

Sanitised fixture: `fixtures/metrics/capabilities_paas_production_observed.json`, generated by `backend/scripts/generate_fixtures.py`. It uses host aliases, the owner-supplied instance example, and truncated IDs only. It contains no image digests or registry names.
