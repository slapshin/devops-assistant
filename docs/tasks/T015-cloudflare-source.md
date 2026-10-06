# T015 — Cloudflare source: edge traffic and security events

Dependencies: T014. Status: see [task index](README.md).

## Outcome

A project can include one Cloudflare zone, optionally narrowed to some of its hostnames. Analyses then cover the zone's edge HTTP traffic (requests, 5xx/4xx, origin errors, cache hit ratio, time to first byte, origin response time) and its security events (blocked and challenged requests). The same deterministic detector produces findings and trends from this data.

## Owner decisions (2026-10-06)

- One Cloudflare zone per project source, optionally filtered by a hostname list.
- Security events (WAF/firewall) are part of the first version.
- No live zone is available for development. Everything is built against Cloudflare's documented GraphQL schema with a mocked transport and synthetic scenarios. Field names that could not be verified are listed under "Live verification" and degrade to `unsupported` capabilities if a real zone rejects them.

## Ownership

`backend/app/domain/{projects,common}.py` (Cloudflare kind, families, entity kinds), new `backend/app/sources/cloudflare/`, `backend/app/metrics/factory.py`, `backend/app/analysis/{derive.py,rules/edge.py,rules/security.py}`, `backend/app/domain/detector_config.py`, `backend/app/api/projects.py`, `backend/app/bootstrap.py`, docs, contracts/fixtures.

## Work

### Domain

- `SourceKind.CLOUDFLARE`. `CloudflareSourceInput { zone_id (32 hex), hostnames: list[str] (0–20, lower-cased, unique), api_token: SecretStr | None (omit to keep), api_url }`. The default `api_url` is `https://api.cloudflare.com/client/v4/graphql`; `synthetic://<scenario>` is also accepted. The read model `CloudflareSource` has `token_set: bool`. `SourceInput` and `Source` become discriminated unions on `kind`.
- `CloudflareConnection` holds the decrypted token. It is never serialised to clients.
- New `SignalFamily`: `edge` (Cloudflare edge HTTP traffic) and `security` (WAF/firewall events). New `EntityKind`: `zone`.
- `MetricSeries.sample_interval: float | None` holds the mean Cloudflare sampling interval (1 = unsampled). When it is high, a confidence reason (`sampled`) lowers confidence. It never changes severity.

### Client and source (`app/sources/cloudflare/`)

- `client.py`: GraphQL over httpx with the bearer token and the existing timeouts. Maps HTTP and GraphQL errors to `SourceError` kinds (auth / unavailable / timeout / bad_response / query). Retries once on rate limiting (`429`), honouring `Retry-After`.
- `catalog.py`: the signal definitions. Each signal has an alias in one GraphQL document per dataset and time chunk, filtered by `datetime_geq/lt` and the hostnames (`clientRequestHTTPHost_in`). Rows are grouped by `datetimeFiveMinutes`.
  - `httpRequestsAdaptiveGroups`: all requests, edge 5xx, 52x (origin errors Cloudflare generates), 404, 4xx, cache hits. `avg.sampleInterval` gives the sampling interval.
  - `httpRequestsAdaptiveGroups` quantiles (Pro plan and above): `edgeTimeToFirstByteMsP95/P99`, `originResponseDurationMsP95`, converted to seconds.
  - `firewallEventsAdaptiveGroups`: events by action, mapped to blocked (`block`, `connectionClose`) and challenged (`challenge`, `jschallenge`, `managedChallenge`).
- `source.py`: `CloudflareMetricsSource`.
  - Capabilities: zone reachable, token valid, datasets enabled, and quantiles allowed. A plan or permission error makes only that signal `unsupported`, with the reason. History is read from the `settings` node (`notOlderThan`).
  - Collection: the 15-day window is split into chunks of at most `min(maxDuration, 1 day)`. Counts per 5-minute bucket are converted to per-second rates. A missing bucket while the zone is reachable means zero requests for count signals. Quantile gaps stay `None`. A result that hits the page limit adds `series_truncated`.
  - The entity is the zone (`zone|zone_id=...`), labelled with its hostname filter.
- `probe.py`: connection test. Checks the token and zone, counts requests in the last hour (`matched_series` = hostnames seen), reports history days and the families.
- `synthetic.py`: `synthetic://healthy|incident|short-history|degraded` for Cloudflare, consistent with the Prometheus scenarios. Demo projects get a synthetic Cloudflare source.

### Detection

- `derive.py`: a traffic spec for `cf_requests` with the ratios 5xx → `edge_server_error_ratio` and 52x → `edge_origin_error_ratio`, the 404 / 4xx rates, `edge_ttfb_p95` (tail p99 as extra evidence), `edge_origin_latency_p95`, and the cache hit ratio `edge_cache_hit_ratio` (direction down).
- `rules/edge.py` and `rules/security.py` with thresholds in `DetectorConfig` and `docs/DECISIONS.md` §5:
  - Edge: request rate (both directions), 5xx ratio, origin error ratio, 404 and 4xx rates (capped at medium), TTFB p95, origin p95, cache hit ratio drop (capped at medium).
  - Security: blocked and challenged request rates (relative only, capped at medium).
- 5xx and 4xx stay separate. Latency comes only from Cloudflare's quantiles, never from counts. Bump `DETECTOR_VERSION`.

### API

- Test connection and health dispatch on the source kind. A synthetic scenario is validated for both kinds. Errors never echo the token.

## Live verification (when a zone and token are available)

- Field names: `datetimeFiveMinutes`, `edgeResponseStatus`, `cacheStatus`, `clientRequestHTTPHost_in`, `quantiles.edgeTimeToFirstByteMsP95`, `quantiles.originResponseDurationMsP95`, `firewallEventsAdaptiveGroups.dimensions.action`, the settings node names.
- Whether `count` is already adjusted for sampling (assumed: yes).
- `maxDuration` and `notOlderThan` on the zone's plan.

## Acceptance

- [x] A Cloudflare source can be created, edited (token kept when omitted), cloned, and tested. The token is encrypted at rest and never returned or logged.
- [x] Against a mocked GraphQL transport, collection splits the window, builds correct 5-minute series, maps auth/plan/rate-limit errors, and marks truncation.
- [x] `synthetic://incident` yields edge 5xx and security findings; `healthy` yields none.
- [x] A project with Prometheus and Cloudflare sources produces one report with both families. A Cloudflare failure makes it `partial`, not `failed`.
- [x] `make contracts`, `make fixtures`, and `make check` pass.

## Completion record

- Completed date: 2026-10-06
- Actual changed files and artifacts:
  - new: `backend/app/sources/cloudflare/` (`api.py`, `catalog.py`, `client.py`, `source.py`, `synthetic.py`, `connect.py`), `app/analysis/rules/edge.py`, `app/analysis/rules/security.py`, `tests/test_cloudflare.py`, fixtures `reports/report_cloudflare.json` (generated by the real pipeline) and `api/connection_test_cloudflare.json`
  - changed: `domain/common.py` (`SourceKind.CLOUDFLARE`, families `edge`/`security`, `EntityKind.ZONE`), `domain/projects.py` (Cloudflare input/read/connection models, tagged source union defaulting to prometheus, `synthetic_url`), `domain/metrics.py` (`sample_interval`), `domain/detector_config.py` (thresholds, `detectors-2026.10.7`), `analysis/derive.py` (Cloudflare traffic spec, extra latencies, per-spec client-error rules), `analysis/engine.py` (`sampled`/`sampled_low` confidence), `storage/projects.py` (per-kind split/view/connection), `metrics/factory.py`, `metrics/probe.py` (`guarded`), `api/projects.py`, `api/problems.py` (union tags dropped from field paths), `bootstrap.py` (demo projects get a synthetic Cloudflare source), `scripts/generate_fixtures.py`
  - docs: `DECISIONS.md` §5, `detection.md`, `metrics-catalog.md`, `OPERATIONS.md` (data sources, token permission), `ARCHITECTURE.md`, `AGENTS.md`
- Commands/checks and results: `make contracts`, `make fixtures`, `make check` pass (814 backend tests, 2 skipped).
- Remaining limitations: built without a live zone. GraphQL field names follow Cloudflare's documentation and are unverified (see "Live verification"). Entities are zone-level (the hostname filter is aggregated, not one entity per hostname). The zone name is not fetched, so entities show the hostnames or a zone ID prefix.
