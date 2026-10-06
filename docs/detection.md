# Detection engine (T005)

`backend/app/analysis/` implements `Detector` as `RobustDetector`: pure numerical code with no AI, UI or I/O. The configuration is `DetectorConfig` (`detectors-2026.10.7`, identified by `config_hash`). Every threshold is a provisional diagnostic heuristic, not an SLO.

## Pipeline

1. **Derive** (`derive.py`): turns collected series into analysable series.
   - Direct resource signals pass through unchanged.
   - HTTP 5xx ratio = `http_5xx / http_requests`, and RPC failure ratio = `rpc_errors / rpc_requests`. A missing numerator counts as 0 only where the denominator was observed. A step is unobserved when requests per step are below 30.
   - `404 rate` and `other 4xx rate` (= 4xx − 404) are derived separately, so 404s are never counted twice.
   - Reverse proxies (Angie zones, Caddy server/handler, Traefik services) are derived the same way as HTTP routes and reuse the `request_rate`, `server_error_ratio`, `not_found_rate`, `client_error_rate` and `latency_p95` rules; their entities have kind `proxy`. nginx stub_status has no status codes or latency, so it yields only the request rate. Proxy connection signals map onto `proxy_connections_active`/`proxy_connections_dropped`, and upstream state (Angie peers, Caddy upstreams, Traefik servers; kind `upstream`) onto the `upstream_unavailable` shortfall rule; `nginx_up = 0` onto `proxy_down`.
   - PostgreSQL (postgres_exporter, kind `database`): server signals (`pg_up`, connections vs `max_connections`, replication lag) and per-database deadlocks, temp-file bytes and the longest open transaction pass through onto the `database_*` rules. The rollback share is `pg_rollbacks / pg_transactions` with the same volume guard as the 5xx ratio (≥ 30 transactions per step).
   - MySQL (mysqld_exporter, kind `database`, server-wide only): `mysql_up`, connections vs `max_connections`, replication lag, row lock waits and on-disk temporary tables pass through; refused connections (`max_connections` reached) use the `database_connections_refused` event rule and stopped replication threads the `database_replication_stopped` shortfall rule. The slow-query share is `mysql_slow_queries / mysql_queries`, volume-guarded at ≥ 30 queries per step.
   - Redis (redis_exporter, kind `database`, server-wide only): `redis_up`, clients vs `maxclients`, memory vs `maxmemory` and evicted keys pass through; rejected connections reuse the `database_connections_refused` event rule, and a replica's broken master link (`database_replica_link_down`) and failed RDB/AOF persistence (`database_persistence_failed`) are shortfall rules. The command rate carries the commandstats mean latency (volume-guarded at ≥ 30 commands per step); the keyspace miss share is `misses / (hits + misses)`, volume-guarded at ≥ 30 lookups per step, and the lookup count itself is not analysed.
   - Cloudflare (kind `zone`, family `edge`): `cf_requests` is the traffic signal (`edge_request_rate`). The 5xx, origin 52x and cache-hit counts become shares of it (`edge_server_error_ratio`, `edge_origin_error_ratio`, `edge_cache_hit_ratio`, the last one direction down), all volume-guarded at ≥ 30 requests per step. 404 and other 4xx rates map onto `edge_not_found_rate`/`edge_client_error_rate`. Edge TTFB p95 (`edge_ttfb_p95`, p99 as evidence) and origin response time p95 (`edge_origin_time_p95`) carry the volume guard. Blocked and challenged firewall events (family `security`) pass through onto `security_blocked_rate`/`security_challenge_rate`.
   - Latency uses p95 (with p99 kept as evidence), or mean when histograms are absent. It carries the same volume guard.
   - Swarm failed-task counts become **positive deltas**: new failures.
2. **Baseline** (`baseline.py`): computed for each trend bucket *b* from `[start_b − 14 d, start_b)` only.
   - Days with ≥ 70 % coverage are adequate.
   - With fewer than 3 adequate days, relative detection is unavailable and only absolute checks run.
   - With ≥ 7 adequate days, the median/MAD is pooled from the same time of day ± 1 h. Otherwise it is pooled over the whole baseline.
   - Scale = `max(1.4826·MAD, abs_floor, 5 % · |median|)`, so a zero MAD is handled explicitly.
3. **Flags** (`detect.py`): a step is anomalous when any of these holds:
   - `|z| ≥ 4` in the rule direction plus the minimum effect (absolute difference and/or relative factor);
   - the value is at or above the absolute heuristic;
   - an event count is > 0 (OOM, new task failures);
   - a replica shortfall, an unavailable upstream, an unreadable proxy status, an unreachable database, stopped replication, a broken replica link or failing persistence of ≥ 1 persists for ≥ 15 min.
4. **Episodes**: flagged steps separated by ≤ 2 steps are merged. A level episode needs ≥ 3 relative steps, or an absolute run of at least the signal's minimum minutes. Episodes are built over the continuous 14-day range, so they can cross bucket boundaries. Trends clip them per bucket, while findings keep the whole episode.
5. **Findings**: one per episode that reaches the latest day.
   - **Severity** is based on magnitude and duration only:
     - peak z 4–6 / 6–10 / ≥ 10 gives 1 / 2 / 3 points; the high / critical heuristic gives 3 / 4;
     - 30–120 min adds 1 point and ≥ 120 min adds 2;
     - events have fixed points.
     - Points map to severity as ≤ 1 low, 2 medium, 3–4 high, ≥ 5 critical.
     - Caps: 4xx/404 medium, network throughput medium, disk throughput low. Throughput alone is informational; saturation is covered by busy time and PSI.
   - **Confidence** is based on data quality only. It is the minimum over: baseline days (≥ 7 / 3–6 / none), coverage in episode ± 1 h (≥ 90 % / ≥ 70 % / lower), requests per step (≥ 300), and episode length (≥ 6 steps). Every lowering factor is listed in `confidence_reasons`.
   - **State:** `ongoing` if still anomalous within 2 steps of T, otherwise `resolved`.
   - **Recurrence:** from the other episodes of the same entity and signal on buckets 1–13: `new` (0 days), `repeated` (1–2 days) or `recurring` (≥ 3 days).
   - **Evidence:** the series over episode ± 6 h, with expected median and band `median ± 4·scale`, the heuristic threshold line, and operands (request rate, p99).
6. **Relations**: findings are related only when their time windows overlap (± 30 min) **and** one of the following holds:
   - they concern the same entity;
   - they concern routes of the same service, or proxy/upstream entities of the same proxy scrape `job`, or database entities of the same server (`job` and `instance`);
   - they share a host through identity labels (node/filesystem/disk/interface/container `instance`) or a verified mapping (OTel `target_info` → container → host, or Swarm task → host).

   Proxy and database entities have no host mapping: an exporter's `instance` is its scrape address, not a verified host.

   Overlap in time alone never relates findings.
7. **Trends**: for each bucket:
   - episodes (≤ 200 listed), anomalous entity-minutes, peak severity and affected entities;
   - observed entity-minutes, the union of observed steps per entity, which is the denominator;
   - `anomalous_share`, the median baseline days, and coverage relative to all entities seen in the 28 days.

   The status is `insufficient_data` when coverage < 50 %, and `insufficient_baseline` when the median baseline days are < 3. The **trend summary** compares the anomalous share of buckets 0–6 with 7–13 (worsening/improving needs ≥ 1.5× and ≥ 0.5 pp). With fewer than 4 OK buckets on either side it is `inconclusive`.
8. **Coverage rows** per family: `unsupported` (capabilities), `anomalous`, `source_error` (collection exclusions), `insufficient_data` (latest-day coverage < 50 % or no baseline), or `no_anomaly`.

IDs are derived from `project|env|T|config_hash` plus entity, signal and start time, so identical inputs give identical findings, episodes and evidence IDs.

## Verification

`backend/tests/test_analysis.py` uses the deterministic `SyntheticMetricsSource` (`backend/app/metrics/synthetic.py`) and covers:

- healthy data; sustained CPU; memory growth (ongoing); disk depletion (critical);
- independent 404 and 5xx bursts; latency shift; mean-only latency; traffic drop;
- the low-volume ratio guard; gaps lowering confidence; zero-variance baselines;
- short retention (5 days) and no baseline (2 days);
- no use of the evaluated day or future data in baselines (tampering tests);
- determinism; ordering; recurrence across days;
- a changing host population, which does not raise the anomalous share;
- relations only through verified mappings; episode merging across the day boundary;
- a Traefik backend outage (upstream unavailable) related to the proxy's 5xx burst; nginx signals mapped to the generic proxy rules; every catalog signal consumed by derivation;
- a PostgreSQL lock pile-up (long transaction, connections, rollbacks, deadlocks) related within one server, and not across servers of the same job;
- a MySQL lock contention (connections, refused connections, slow queries, lock waits) related within the primary only; both replication status syntaxes mapped onto the same rules;
- a Redis eviction storm (memory, evictions, miss share, command latency) related within the master only; a broken replica link as a shortfall;
- severity mapping; the inconclusive trend summary;
- composing a valid `AnalysisReport`.

Live check (2026-09-30, paas/production, 360 series): detection took 4.3 s and produced 9 latest-day findings. One was medium (an other-4xx spike on `POST /api/v3/tasks`) and eight were low (disk-throughput bursts, recurring). The 14-day trend was stable (0.25 % vs 0.31 % anomalous share), all 14 buckets OK with 14 baseline days.
