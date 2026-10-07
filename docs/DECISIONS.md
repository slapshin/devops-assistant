# Technical decisions (T001)

Status: recorded 2026-09-30 by T001; frontend framework changed to Vue.js by owner decision the same day. Everything here, except items marked **owner decision**, is an **implementation default** chosen by the implementing agent under the [architecture](ARCHITECTURE.md) version policy — not an owner-confirmed requirement. Confirmed requirements remain those in the [product plan](PRODUCT_PLAN.md#confirmed-requirements). Later tasks may revise a default by editing this file with a reason; T002 pins what is recorded here.

Companion document: [UI specification](UI_SPEC.md).

## 1. Runtimes, tooling, and versions

Lookup date for every version below: **2026-09-30**. Sources: PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`), npm registry (`https://registry.npmjs.org/<pkg>/latest`), Node.js release index (`https://nodejs.org/dist/index.json`), Python release cycles (`https://endoflife.date/api/python.json`, cross-checked with python.org), Docker Hub official image tags. T002 re-checks these when pinning; a newer patch release may replace a listed one without a new decision.

### Backend

| Component | Version | Rationale |
| --- | --- | --- |
| Python | 3.14.7 | Latest stable release line (EOL 2030-10); all selected packages publish 3.14 support/wheels (numpy has cp314 manylinux wheels). `uuid.uuid7()` in the standard library gives time-ordered IDs. |
| uv | 0.12.x (local 0.12.19) | Project/venv manager; `uv.lock` committed; `uv sync --locked` in CI and Docker. |
| FastAPI | 0.142.1 | Proposed default stack; OpenAPI generation feeds frontend types. |
| Uvicorn | 0.54.0 | ASGI server for native and container runs. |
| Pydantic | 2.13.5 | Contract models, validation, JSON Schema export. |
| pydantic-settings | 2.15.0 | Environment configuration with actionable validation errors. |
| httpx | 0.28.1 | Async metrics API client with timeouts and cancellation. |
| numpy | 2.5.3 | Vectorised median/MAD and window statistics. No pandas; the data volumes are bounded. |
| SQLAlchemy (Core only) | 2.1.1 | Explicit SQL for jobs/reports without an ORM layer; required by Alembic. |
| Alembic | 1.20.0 | Explicit, versioned SQLite migrations run at startup (upgrade only, never drop). |
| openai | 3.22.1 | Only imported inside `backend/app/ai/openai_adapter.py`. Uses the Responses API with `client.responses.parse(..., text_format=<Pydantic model>)` for structured output (OpenAI Python SDK docs, `helpers.md`). |
| pytest | 9.1.1 | Tests; plus pytest-asyncio 1.4.0, jsonschema 4.26.0 (fixture validation), and types-jsonschema (pinned by T002). |
| ruff | 0.16.9 | Lint + format. |
| mypy | 2.3.1 | Strict type checking of `backend/app`. |
| basedpyright | 1.40.2 | Complements mypy: pyright `standard` checks plus `reportDeprecated` (newer typeshed). |

### Frontend

| Component | Version | Rationale |
| --- | --- | --- |
| Node.js | 24.21.0 LTS ("Krypton") | Newest LTS line. Node 26.x is the Current line and not LTS until its scheduled promotion, so it is not chosen for a self-hosted build. Meets engines of Vite 8, Vitest 5, @vitejs/plugin-vue 6, ESLint 10. |
| npm | bundled with Node 24 | `package-lock.json` committed; `npm ci`. pnpm is not installed locally and adds nothing needed here. |
| Vue | 3.5.43 | **Owner decision (2026-09-30): Vue.js instead of React.** Latest stable line; 3.6 is still in pre-release (`rc`), so it is not used. Composition API with `<script setup lang="ts">` single-file components. |
| TypeScript / vue-tsc | **6.0.3** / 3.3.11 | Compatibility exception: TypeScript 7.0.2 is `latest`, but `typescript-eslint` 8.71.0 (used by `@vue/eslint-config-typescript` 14.9.0) declares `typescript >=4.8.4 <6.1.0`. Use 6.0.x until typescript-eslint supports 7. `vue-tsc` (peer `typescript >=5.0.0`) type-checks `.vue` files. `@vue/tsconfig` 0.9.1 is the base config. |
| Vite / @vitejs/plugin-vue | 8.3.1 / 6.0.9 | Dev server with `/api` proxy to the backend; static production build. |
| Vue Router | 5.3.1 | URL-addressable reports, views, and findings (HTML5 history mode, no SSR). The optional Pinia/Colada peers are not used. |
| TanStack Query (`@tanstack/vue-query`) | 5.104.0 | Server state, job polling, retry/cancel. No Pinia store: there is no client state beyond URL and query cache. It pulls `vue-demi` 0.14.10, whose npm postinstall is blocked by npm 11's install-script policy; the published default build already targets Vue 3 (`isVue3 = true`, checked 2026-09-30), so no script approval is needed. |
| Apache ECharts / vue-echarts | 6.1.0 / 8.3.1 | Time-series with gaps (`null`), expected-range bands (stacked area / `markArea`), `dataZoom`, SVG renderer, and built-in `aria` descriptions. Chosen over uPlot (no a11y layer) and Recharts (weaker large time-series handling). |
| Styling | Plain CSS with custom properties; component styles in SFC `<style scoped>` | No CSS framework; tokens defined once in `frontend/src/styles/tokens.css`. Shared dashboard classes (panels, 24-column grid, chips, grid table) live in `frontend/src/styles/base.css`; the visual design is the Claude Design "DevOps Assistant UI" canvas (2026-10-05). |
| IBM Plex Sans / Mono (`@fontsource/ibm-plex-sans`, `@fontsource/ibm-plex-mono`) | 5.3.0 / 5.3.0 | Typeface of the UI design. Self-hosted from the bundle (Latin subset, weights 400–600 / 400–500) so the local app makes no third-party font requests and works offline. |
| openapi-typescript | 7.13.0 | Generates `frontend/src/api/schema.d.ts` from the backend OpenAPI document; committed and drift-checked in CI. Compatibility exception (T002): its peer range is `typescript ^5.x` with no release supporting 6.x, so `package.json` `overrides` points it at the pinned TypeScript 6.0.3; generation is verified by `npm run check:api`. |
| Vitest / Testing Library | 5.0.2 / @testing-library/vue 8.1.0 (+ @vue/test-utils, jest-dom 7.0.1) | Unit/component tests using accessible role/text queries. |
| Playwright | 1.63.0 | Desktop + narrow-viewport workflow and keyboard checks (T008/T010). |
| ESLint / eslint-plugin-vue / @vue/eslint-config-typescript | 10.11.0 / 10.11.1 / 14.9.0 | Lint with `flat/recommended` and zero warnings allowed. Two layout-only rules (`max-attributes-per-line`, `singleline-html-element-content-newline`) are off. |

### Containers and storage

- Runtime image `python:3.14.7-slim-trixie` (tag exists on Docker Hub). The frontend build stage uses `node:24.21.0-trixie-slim`: T002 verified `npm ci && npm run check` in that image. Local development on Node 26 also passes, but Node 24 is the reference. Single runtime container: FastAPI serves the built frontend as static files.
- SQLite via the Python standard library driver (local sqlite3 3.54 observed; the image's bundled version is recorded by T009). WAL mode, `foreign_keys=ON`, `busy_timeout=5000`. Database path `DATA_DIR/assistant.sqlite3`; `DATA_DIR` is a persistent volume in Docker.

### Schema tooling

Pydantic models in `backend/app/domain/` are the single source of truth. T002 exports:

1. `docs/contracts/*.schema.json` — JSON Schema for each shared contract (used to validate fixtures).
2. `/api/openapi.json` — FastAPI OpenAPI, from which frontend TypeScript types are generated.

CI fails if generated schema/types differ from committed files. Saved reports carry `schema_version` (see §4).

## 2. Configuration

All settings come from environment (optionally the repository-root `config.env`, never committed; template `config.env.template`). Invalid settings fail startup with the variable name, the bad value (secrets masked), and the expected form.

| Variable | Default | Notes |
| --- | --- | --- |
| `METRICS_URL`, `METRICS_BEARER_TOKEN` / `METRICS_BASIC_AUTH_USER` + `METRICS_BASIC_AUTH_PASSWORD`, `METRICS_TLS_VERIFY` | unset | **Deprecated (T012).** Metrics sources are configured per project. Read once after migration 0003, to give projects created from pre-project reports the source they came from; ignored afterwards. Validation is unchanged (no credentials in the URL; auth methods exclusive). |
| `DEMO_PROJECTS` | `false` | Seed one `synthetic://<scenario>` project per scenario when no project exists (`make demo`). |
| `AI_PROVIDER` | `openai` | `openai`, `none`, or `fake`. `none` produces numerical reports with explanation status `disabled`. `fake` is a deterministic template provider for tests and offline demos; it makes no network calls. |
| `OPENAI_API_KEY` | unset | If `AI_PROVIDER=openai` and the key is missing, the app starts and reports explanation status `not_configured` (numerical analysis must still work). |
| `OPENAI_MODEL` | unset | Required for explanations; no model name is hard-coded in code. It must support Structured Outputs on the Responses API. No key was available during T006, so choose the model per account and check it with `uv run python -m scripts.check_openai [--structured]`. |
| `OPENAI_BASE_URL` | unset | Optional, for compatible gateways. |
| `DATA_DIR` | `./data` | SQLite database and the generated `secret.key`. |
| `SECRET_KEY` | unset | Fernet key (32 url-safe base64 bytes) encrypting stored project secrets. Unset: generated once into `DATA_DIR/secret.key` (mode 0600). A lost or changed key makes stored secrets unreadable (projects report `credentials_readable: false`) but never stops startup (T011). |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | Local-only binding by default (no auth in this release). |
| `DETECTOR_CONFIG_FILE` | unset | Optional path to a **JSON** override of §5 defaults (T009: JSON only, to avoid a YAML dependency). A changed config yields a new `config_hash` in reports. |
| `UI_STATIC_DIR` | `../frontend/dist` if built | Built UI served at `/` with an SPA fallback (T009). |
| `LOG_LEVEL` | `INFO` | Structured JSON logs; query strings logged, credentials never. |

## 3. API conventions

- Prefix `/api`; JSON bodies; `snake_case` fields; timestamps are RFC 3339 UTC with `Z` (`2026-09-30T10:05:00Z`); durations in seconds (integers) unless a field name says otherwise; IDs are UUIDv7 strings.
- Errors use RFC 9457 `application/problem+json`: `{type, title, status, detail, code, errors?}` where `code` is a stable machine value.

| Route | Result |
| --- | --- |
| `GET /api/health` | `{status, version, database}`. Source reachability is per project (`GET /api/projects/{id}/health`). |
| `GET /api/config` | Public runtime facts only: AI provider/status, model name, detector version, config hash, limits. No secrets. |
| `GET /api/projects` | `{items: [ProjectSummary]}` sorted by name: the project plus `latest_analysis` (newest finished job) and `active_analysis` (queued/running job). Secrets are never returned: auth shows only `token_set` / `password_set`. |
| `POST /api/projects` | Body `ProjectInput {name, description?, matchers: [{name, value}] (1–10, equality only), sources: [{kind: "prometheus", url, tls_verify, auth}]}` → `201 Project`. Name is unique case-insensitively → `409 project_name_taken`. |
| `GET / PUT / DELETE /api/projects/{id}` | Read, full replace, hard delete (`204`; also deletes the project's analyses and reports; `409 project_busy` while one is queued or running). On PUT an omitted secret keeps the stored one when the auth type is unchanged; otherwise `422` names the missing field. |
| `POST /api/projects/test-connection` | Body `{project_id?, matchers, source}` → `ConnectionTest {reachable, auth_ok, matched_series, history_days, families, message, checked_at}`. Bounded (20 s), read-only; `project_id` fills omitted secrets from the stored project. |
| `GET /api/projects/{id}/health` | Cached (60 s, reset on update) connection test of the stored project; `null` without a metrics source. |
| `POST /api/analyses` | Body `{project_id, end_time?}` → `202 {analysis}`; duplicate active job → `200 {analysis, duplicate_of_active: true}`. `409 source_not_configured` / `credentials_unreadable` when the project cannot be analysed. Jobs carry `daily_episodes` (14 values, oldest first) once a report is saved. |
| `GET /api/analyses?project_id=&limit=&cursor=` | Saved/recent analyses, newest first (reopening reports). Default limit 20, max 100. |
| `GET /api/analyses/{id}` | Job status and progress (polled by the UI every 2 s while active). |
| `GET /api/analyses/{id}/report` | Report snapshot; `409 report_not_ready` while active; `404 report_unavailable` for failed/cancelled jobs without a report. |
| `DELETE /api/analyses/{id}` | Cancels an active job → `202`; terminal job → `409 analysis_not_active`. Does not delete saved reports (deletion is out of scope; see §6 retention). |

Error codes: `validation_error` (422), `project_not_found` (404), `project_name_taken` (409), `project_busy` (409), `source_not_configured` (409; also a job error code), `credentials_unreadable` (409: stored secrets cannot be decrypted; re-enter them; also a job error code), `analysis_not_found` (404), `report_not_ready` (409), `report_unavailable` (404), `analysis_not_active` (409), `queue_full` (429, `Retry-After`), `metrics_source_unavailable` (job error code), `end_time_invalid` (422: future, older than 90 days, or not aligned — it is floored to the step), `internal_error` (500, with log correlation ID).

## 4. Jobs, persistence, and lifecycle

**States:** `queued → running → completed | partial | failed | cancelled`.

- `completed`: all *supported* signal families were collected and analysed. Unsupported/insufficient signals do not make a job partial — they are expected coverage facts.
- `partial`: a report was saved but some supported families hit source errors, timeouts, or budget truncation. The report lists each omission.
- `failed`: no usable report (e.g. source unreachable at collection start, internal error, restart interruption).
- `cancelled`: user cancellation; no report is saved (`error` is null). T007 cancels a running job by cancelling its task, which aborts in-flight source/provider requests immediately; the token is also checked between stages.
- Explanation state is **separate**: `disabled | not_configured | pending | succeeded | failed | skipped_no_findings`. AI failure never changes a numerical `completed` to `failed` or `partial`.

**Stages and progress** (stored on the job, exposed by `GET /api/analyses/{id}`): `discovery → collection → detection → trends → explanation → saving`, each `{stage, status, started_at, finished_at, done, total, message}`.

**Runner:** one in-process asyncio worker; **1 running job**, **up to 4 queued** (configurable). Beyond that → `429 queue_full`. Hard job timeout 10 minutes → `failed` with `job_timeout` (or `partial` if detection already produced a saveable report).

**End time:** `T = floor(end_time or now, 5 min)`, frozen at submission and used for every query, window, trend bucket, and evidence ID in the job.

**Duplicate submissions:** a submission whose `(project, env, T, config_hash)` equals a queued/running job returns that job (200). Completed reports are not deduplicated — a new explicit run creates a new report.

**Cancellation:** cooperative. The worker checks a cancel flag between queries and stages and cancels in-flight HTTP requests (task cancellation). State becomes `cancelled` within ≈ one query timeout. Queued jobs cancel immediately.

**Restart handling:** at startup, any `queued` or `running` job is marked `failed` with `code=interrupted_by_restart` and an explanatory message. Jobs are not auto-resumed (the frozen `T` would still be valid, but silently re-querying after restart surprises users). The UI offers "Run again".

**Tables (Alembic-managed):** `analysis_jobs` (id, project, env, end_time, config_hash, detector_version, state, stages JSON, error, created/started/finished timestamps), `reports` (analysis_id PK/FK, schema_version, created_at, size_bytes, report JSON compressed with zlib), `schema_meta`. Report snapshots are immutable after save; the explanation is written in the same transaction as the report (explanation runs before `saving`).

**Report schema version:** `schema_version: "1.0"`. Minor bumps add optional fields; major bumps require a reader migration. Readers must open every saved major version they claim to support, or return `report_unavailable` with `code=schema_unsupported` — never a crash.

## 5. Numerical policy (detector config `detectors-2026.10.9`)

All thresholds are **provisional diagnostic heuristics**, configurable through `DETECTOR_CONFIG_FILE`, and labelled in the UI as heuristics — never SLOs or SLO violations. Every report records `detector_version` and `config_hash`.

### Windows and resolution

- Latest day `[T-24h, T)`; trend buckets `B_i = [T-(i+1)·24h, T-i·24h)`, `i = 0..13` (`B_0` is the latest day, so the latest-day findings and trend day 14 use identical calculations).
- Collection range `[T-28d-lookback, T)` at **step 300 s**. Rate window `max(300s, 4 × scrape_interval)`. T003 verified a 10 s scrape interval for every paas/production job, so the window is 300 s. The source's 4-week retention puts `T-28d-5m` at the edge of the data: the oldest trend bucket's first baseline day may be partially covered, and the coverage rules below account for that rather than a longer range.
- Baseline for a bucket: preceding days only, up to 14 days `[start-14d, start)`. Never the bucket itself or later data.

### Coverage

- A step is *observed* for a series if a sample exists at that step.
- A baseline day is *adequate* if its coverage ≥ 70 %.
- Relative detection requires **≥ 3 adequate baseline days** (provisional minimum). Otherwise that series/bucket is `insufficient_baseline`: absolute checks still run, and relative detection is reported as unavailable (not healthy).
- An analysed bucket with coverage < 50 % for a series is `insufficient_data` for that series; episodes found in its observed part still count but carry low confidence.

### Baseline and scoring

- Robust scale `s = max(1.4826 · MAD, abs_floor[signal], 0.05 · |median|)`. The floors handle zero MAD (flat series) explicitly; a flat baseline with `abs_floor` prevents division by zero and infinite scores.
- Score `z = (x − median) / s`, direction per signal (below).
- Time-of-day bands: used only when **≥ 7 adequate baseline days**; median/MAD pooled from the same hour-of-day ±1 h across baseline days. Otherwise whole-baseline statistics. The report records which mode applied. No weekly seasonality.
- Anomalous step: `z ≥ 4` in the signal's direction **and** the minimum effect in the table below.
- Episode: ≥ **3 consecutive** anomalous steps (15 min); episodes separated by ≤ 2 non-anomalous steps are merged. An episode crossing a bucket boundary is split for trend counting and kept whole for evidence.

### Signals

| Category | Signal (entity) | Direction | Min effect (relative detector) | Absolute check (no baseline needed) |
| --- | --- | --- | --- | --- |
| CPU | non-idle utilisation from `node_cpu_seconds_total` (`1 - idle` rate per instance; iowait and steal reported separately) | up | +10 pp | ≥ 90 % for ≥ 15 min |
| CPU | iowait share | up | +5 pp | ≥ 20 % for ≥ 15 min |
| Memory | `1 - MemAvailable/MemTotal` | up | +10 pp | available < 10 % for ≥ 15 min |
| Filesystem | used ratio / inode used ratio (device, mountpoint; excluding fstypes tmpfs/overlay/nfs4 by default; T003) | up | +5 pp | free < 10 % (high) / < 5 % (critical) |
| Disk I/O | device busy time ratio, read/write bytes (excluding `sr*` optical devices) | up | +20 pp busy | busy ≥ 90 % for ≥ 15 min |
| Network | rx/tx bytes, errors, drops per device (excluding `lo`) | both (bytes), up (errors/drops) | ×2 or ÷2 bytes; errors/drops > 0 when baseline ≈ 0 | — |
| Container | CPU usage, memory working set per swarm task/container (`name`), restarts (swarm task changes, `container_start_time_seconds` changes), OOM (`container_oom_events_total`) | up | +25 % | any restart/OOM increase. Memory ≥ 90 % of limit and throttling ≥ 25 % apply only when limits/CFS metrics exist. T003 found neither for paas/production, so those are `unsupported` there. |
| HTTP/RPC traffic | request rate (service, route, method) | both | ×2 or ÷2 and ≥ 0.2 req/s change | — |
| HTTP/RPC failure | 5xx ratio (`http_response_status_code=~"5.."` / all); RPC failure ratio (`rpc_response_status_code!="OK"` / all). Baselines are ≈ 0 in practice (T003), so the absolute check is the main detector. | up | +2 pp | ≥ 5 % over a step window with ≥ 30 requests |
| HTTP client errors | 404 rate and other-4xx rate (4xx − 404), each ×2 and ≥ +0.1 req/s (T005: rates replace the 4xx ratio to avoid double counting) | up | ×2 | — (never a failure) |
| Reverse proxies | nginx (stub_status exporter), Angie (`prometheus_all.conf`), Caddy, Traefik (per server zone / server+handler / service): request rate, 5xx ratio, 404 and other-4xx rates, and p95/p99 where the proxy exports histograms (Caddy, Traefik). These reuse the HTTP rules above. | as HTTP | as HTTP | as HTTP |
| Proxy health | open connections (nginx, Angie, Traefik entrypoints); dropped connections (nginx accepted − handled, Angie `dropped`) | up | open: ×2 and ≥ +20; dropped: ≥ +0.1/s | — |
| Proxy health | upstream unavailable (Angie peer state unavailable/unhealthy, Caddy upstream unhealthy, Traefik server down); nginx status unreadable (`nginx_up = 0`) | up | — | value ≥ 1 for ≥ 15 min (shortfall rule, 3 points) |
| PostgreSQL server | unreachable (`pg_up = 0`) | up | — | value ≥ 1 for ≥ 15 min (shortfall rule, 3 points) |
| PostgreSQL server | connections (`Σ numbackends / max_connections`) | up | +10 pp | ≥ 80 % high, ≥ 95 % critical, for ≥ 5 min |
| PostgreSQL server | replication lag (`pg_replication_lag_seconds`, 0 on a primary) | up | ×2 and ≥ +30 s | ≥ 300 s for ≥ 15 min |
| PostgreSQL database | transaction rate (commit + rollback; severity ≤ medium) | both | ×2 or ÷2 and ≥ 1 tx/s change | — |
| PostgreSQL database | rollback share (rollback / all transactions, volume-guarded at ≥ 30 transactions per step). ORMs roll back routinely, so there is no absolute check. | up | ×2 and ≥ +5 pp | — |
| PostgreSQL database | deadlocks | up | — | any deadlock in a step (event rule, 2 points) |
| PostgreSQL database | temporary file bytes (work_mem spills; severity ≤ medium); longest open transaction, incl. idle in transaction | up | temp: ×2 and ≥ +1 MiB/s; longest: ×2 and ≥ +60 s | — |
| MySQL server | unreachable (`mysql_up = 0`); connections (`Threads_connected / max_connections`); replication lag (`Seconds_Behind_Master`/`_Source`) | up | as PostgreSQL | as PostgreSQL |
| MySQL server | refused connections (`Connection_errors_max_connections`) | up | — | any refusal in a step (event rule, 2 points) |
| MySQL server | replication stopped (SQL or I/O thread not running, incl. Connecting) | up | — | value ≥ 1 for ≥ 15 min (shortfall rule, 3 points) |
| MySQL server | query rate (`Questions`; severity ≤ medium) | both | ×2 or ÷2 and ≥ 5 q/s change | — |
| MySQL server | slow-query share (`Slow_queries / Questions`, volume-guarded at ≥ 30 queries per step) | up | ×2 and ≥ +1 pp | — |
| MySQL server | InnoDB row lock waits; on-disk temporary tables (severity ≤ medium) | up | ×2 and ≥ +1/s | — |
| Redis server | unreachable (`redis_up = 0`); clients (`connected_clients / maxclients`) | up | as PostgreSQL | as PostgreSQL |
| Redis server | rejected connections (`rejected_connections`, maxclients reached) | up | — | any rejection in a step (event rule, 2 points) |
| Redis server | replica link to master down; persistence failing (last RDB bgsave or AOF write not `ok`) | up | — | value ≥ 1 for ≥ 15 min (shortfall rule, 3 points) |
| Redis server | memory vs `maxmemory` (only where set). No absolute check: an LRU cache sits at maxmemory by design. | up | +10 pp | — |
| Redis server | command rate (severity ≤ medium) | both | ×2 or ÷2 and ≥ 5 cmd/s change | — |
| Redis server | evicted keys (severity ≤ medium) | up | ×2 and ≥ +1/s | — |
| Redis server | keyspace miss share (`misses / (hits + misses)`, volume-guarded at ≥ 30 lookups per step; severity ≤ medium) | up | ×1.5 and ≥ +10 pp | — |
| Redis server | mean command latency (commandstats `usec / calls`, volume-guarded at ≥ 30 commands per step) | up | ×2 and ≥ +0.5 ms | — |
| Cloudflare edge (zone) | request rate | both | ×2 or ÷2 and ≥ 0.2 req/s change | — |
| Cloudflare edge (zone) | 5xx share (edge status 500–599, volume-guarded at ≥ 30 requests per step) | up | +2 pp | ≥ 5 % for ≥ 5 min |
| Cloudflare edge (zone) | origin error share (520–530: origin down, timed out, TLS failure; volume-guarded) | up | +1 pp | ≥ 2 % for ≥ 5 min |
| Cloudflare edge (zone) | 404 rate; other 4xx rate (severity ≤ medium; WAF blocks answer 403, so they also count here) | up | ×2 and ≥ +0.1/s | — |
| Cloudflare edge (zone) | cache hit share (hit, stale, updating, revalidated / all requests; volume-guarded; severity ≤ medium) | down | −15 pp | — |
| Cloudflare edge (zone) | edge TTFB p95 (p99 as evidence); origin response time p95 (volume-guarded; Pro plan and up) | up | ×1.5 and ≥ +50 ms | — |
| Cloudflare security (zone) | blocked requests (block, connection close); challenges issued (challenge, JS, managed). Mitigated traffic is not an outage: severity ≤ high | up | ×3 and ≥ +0.1/s | — |
| Sentry errors (application) | error events; unhandled errors (`error.unhandled:true`) | up | ×3 and ≥ +0.02/s (unhandled: ≥ +0.01/s) | — |
| Sentry errors (application) | users hitting errors per 5 min (`count_unique(user)`) | up | ×2 and ≥ +5 users | — |
| Sentry transactions (application) | throughput (`is_transaction:true` spans, extrapolated from sampled traces) | both | ×2 or ÷2 and ≥ 0.2/s change | — |
| Sentry transactions (application) | failed share (`failure_rate()`: every non-ok status, including client-side ones such as `not_found`; volume-guarded at ≥ 30 transactions per step) | up | +3 pp | ≥ 10 % for ≥ 10 min |
| Sentry transactions (application) | duration p95 (p99 as evidence; volume-guarded) | up | ×1.5 and ≥ +50 ms | — |
| Wazuh host security (agent) | all alerts; authentication failures (`rule.groups`: `authentication_failed`, `authentication_failures`, `invalid_login`). An alert surge calls for investigation, not an outage: severity ≤ high | up | ×3 and ≥ +0.03/s (9 per 5 min) | — |
| Wazuh host security (agent) | high-level alerts (`rule.level` ≥ 12; severity ≤ high) | up | ×3 and ≥ +0.005/s | — |
| Wazuh file integrity (agent) | FIM changes (`rule.groups: syscheck`). Routine updates also change files: severity ≤ medium | up | ×3 and ≥ +0.05/s (15 per 5 min) | — |
| Latency | p95/p99 from verified classic histograms (HTTP and RPC buckets 0.005–10 s; values at the top bucket are reported as "≥ 10 s"); mean from sum/count only | up | ×1.5 and ≥ +50 ms | — |

- Rates are computed before aggregation; ratio numerator and denominator use the same selector scope (project, env, and entity labels).
- A ratio step is evaluated only when the denominator has ≥ 30 requests in the step window; otherwise the step is not observed for that ratio (reduces coverage, not healthy).
- `error_type` is reported as an attribute; it never classifies a request as a server failure on its own (e.g. `error_type="404"`).
- Latency without histogram buckets or native histograms is `unsupported` with the reason; count-only data never yields latency.
- Route-level analysis for the top 20 routes by 14-day volume per service; remaining routes aggregated as `(other routes)`.
- Cloudflare data comes from adaptive (sampled) datasets. Counts are Cloudflare's estimates. A mean sampling interval of ≥ 10 lowers a finding's confidence (`sampled`), and ≥ 100 makes it low (`sampled_low`); sampling never changes severity. Inside a fetched chunk a bucket without rows means zero events, while periods outside retention or of failed queries stay unknown.
- Sentry transaction counts are extrapolated by Sentry from the client's trace sample rate; a changed sample rate shows up as a throughput change. Error events are counted as stored (after inbound filters and rate limits). Before the project's creation date values are unknown, never zero; a project without transactions in the last 24 h has its transaction signals `unsupported` ("tracing not set up").
- Wazuh alerts are counted as indexed (level ≥ 3 by default, the manager's `log_alert_level`). An agent's values are unknown before its first alert in the 28-day window, never zero, so a new or long-quiet agent gets a short baseline instead of a fake quiet one. A search that timed out or failed on some shards is a failed chunk, never a partial count. Agents are never matched to Prometheus nodes by name.

### Severity (magnitude and duration only)

Points = magnitude points + duration points; severity from points.

| Magnitude | Points | Duration | Points |
| --- | --- | --- | --- |
| peak z 4–6 | 1 | 15–30 min | 0 |
| peak z 6–10 | 2 | 30–120 min | 1 |
| peak z ≥ 10 | 3 | ≥ 120 min | 2 |
| absolute check "high" | 3 | | |
| absolute check "critical" | 4 | | |

`≤ 1` low · `2` medium · `3–4` high · `≥ 5` critical. HTTP 4xx-only findings are capped at **medium**. T005 adds two more caps: network throughput at medium, and disk throughput at low. Throughput alone is informational; saturation is judged by busy time and PSI. Events have fixed points: OOM kills 3, new failed Swarm tasks 2, replica shortfall ≥ 15 min 3. The implemented policy is in [detection.md](detection.md).

### Confidence (data quality only)

Confidence = the minimum of these factors; every lowering factor is listed in `confidence_reasons`:

| Factor | High | Medium | Low |
| --- | --- | --- | --- |
| Adequate baseline days | ≥ 7 | 3–6 | absolute check only |
| Coverage in episode ± 1 h | ≥ 90 % | 70–90 % | < 70 % |
| Request volume (ratios) | ≥ 300 per step | 30–300 | — |
| Episode length | ≥ 6 steps | 3–5 steps | — |

### Correlation

Findings are grouped only when they share identity labels (same `(job, instance)`, same device, or an explicit mapping). Overlapping time windows across unrelated entities may be mentioned only as a hypothesis ("coincides with"), never as attribution. The node instance `paas-production` and HTTP instance `10.0.4.251:5555` are not related by name. T003 verified a label mapping, applied per time step: `target_info.service_instance_id` → cAdvisor `id` → cAdvisor `instance` = node `instance`. Swarm `docker_swarm_task_info.node_hostname` gives a second one. This is how service findings may be attributed to a host ([telemetry inventory §5](telemetry-inventory.md#5-identity-and-cross-layer-mappings)).

### Trends

Per bucket: `episode_count`, `anomalous_minutes`, `peak_severity`, `affected_entities`, `observed_entity_minutes` (eligible denominator), `anomalous_share = anomalous_minutes / observed_entity_minutes`, `baseline_days_used`, and coverage status. Buckets without enough baseline are marked `insufficient_baseline`, not zero.

## 6. Query, resource, and storage budgets

| Budget | Default |
| --- | --- |
| Per-query timeout | 30 s (equals the source's `search.maxQueryDuration=30s`) |
| Range chunking | Split every range query into ≤ 7-day chunks, merged client-side. T003 measured 22.6 s for one 28-day CPU query on paas-gpu/production. |
| Concurrent queries per job | 4 |
| Max series per query | 500 → stop, mark family `truncated`, job `partial` |
| Max series per job | 5,000 |
| Max points per series | 30 days × 288 = 8,640 (source limit `search.maxPointsPerTimeseries=30000`, verified) |
| Discovery lookback | 28 days, results cached 5 min per scope |
| Label value cardinality guard | max 200 projects / 50 envs per project shown; excess reported |
| Evidence per finding | up to 3 series; window = episode ± 6 h at 300 s step, with baseline median and expected band |
| Trend data | daily aggregates plus per-episode summaries (entity, category, span, severity, peak observed/expected) for all 14 buckets; raw evidence series only for latest-day findings |
| Report size | 20 MB uncompressed max; if exceeded, drop evidence of lowest-severity findings first **and list each dropped item in `exclusions`** (never silently) |
| AI input | top 20 findings by severity, ≤ 40,000 characters of evidence summaries; no raw series, no credentials, no URLs with secrets |
| AI call | timeout 60 s, 1 retry on transient errors |

Retention: saved reports are **not deleted automatically** in this release. `GET /api/config` exposes report count and database size. T009 added `python -m app.maintenance prune --older-than N` (a dry run unless `--yes`) and `backup <file>` (SQLite online backup); see [OPERATIONS.md](OPERATIONS.md).

All queries are read-only (`/api/v1/query`, `/query_range`, `/series`, `/labels`, `/label/<name>/values`, `/metadata`, `/status/buildinfo`). Every selector includes `project="<p>", env="<e>"`; the query builder rejects a selector without both (enforced by tests in T004).

## 7. AI explanations

Implemented in T006 (`backend/app/ai/`):
- `OpenAIExplanationProvider` calls `responses.parse` with a strict JSON Schema, `store=False`, at most 2,000 output tokens, a 60 s timeout, and 1 SDK retry. The SDK's own `httpx2` stack is used; it is the only module importing `openai`.
- `FakeExplanationProvider` is used for tests and demos.
- `explain_findings` never raises. Every failure becomes `status=failed` with a reason: `timeout`, `rate_limited`, `authentication_failed`, `permission_denied`, `model_not_found`, `unavailable`, `refusal`, `incomplete: <reason>`, `invalid_output`, or `provider_error_<status>`. An overall 150 s guard applies.
- Label and title text is cleaned (control characters removed, URL credentials redacted, 200 characters per label). The instructions declare it untrusted data.

- `ExplanationProvider` interface in the domain; `OpenAIExplanationProvider` is the only file importing `openai`.
- Input: scope, windows, coverage summary, and finding digests (ID, entity, category, severity, confidence, observed/expected values, time span). Output model (Pydantic, strict): `summary`, `hypotheses[{text, finding_ids[], likelihood: plausible|possible|speculative}]`, `investigation_steps[{text, finding_ids[]}]`, `uncertainty`, `provider`, `model`.
- Validation: any hypothesis/step referencing an unknown finding ID is dropped and counted in `explanation.validation_notes`; if nothing valid remains, status `failed` with reason `invalid_output`. The UI always labels hypotheses as unverified hypotheses.
- With no findings, the provider is not called (`skipped_no_findings`); the report states that no anomalies were detected within coverage.

## 8. Code layout (for T002)

```
backend/app/{domain,metrics,analysis,ai,api,storage,settings.py,main.py}
backend/migrations/        (Alembic)
backend/tests/
frontend/src/{api,routes,components,charts,styles}
fixtures/                  (synthetic, sanitised telemetry and reports)
docs/contracts/            (generated JSON Schema)
```

## 9. Revisit triggers

- Applied from T003 (2026-09-30): 10 s scrape interval, 4-week retention, container/RPC metric names, classic histograms, range chunking. Revisit if another scope differs (runtime discovery must not assume paas/production capabilities).
- typescript-eslint supports TypeScript 7 → drop the TypeScript 6.0 exception.
- openapi-typescript declares TypeScript 6+ support → remove the npm `overrides` entry.
- Node 26 becomes LTS (scheduled October 2026) → consider moving from 24.
- Access beyond localhost requested → separate access-control decision before binding to `0.0.0.0` outside Docker.
