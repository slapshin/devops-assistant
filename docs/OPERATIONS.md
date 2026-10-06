# Operations

The assistant is a single process: a FastAPI backend that also serves the built Vue UI. It stores job status and report snapshots in SQLite. It only **reads** from the metrics source and never writes to it. It is meant for **local use**: there is no authentication, so do not expose it beyond your machine without adding access control first.

## Quick start

### Docker (recommended)

```sh
cp config.env.template config.env  # optional: OPENAI_API_KEY, OPENAI_MODEL, …
make up                            # http://127.0.0.1:8000
make logs
make down                          # keeps the data volume (reports survive)
```

- The image is built from `devops/docker/Dockerfile` (context: repository root); the compose file is `tools/compose/compose.yml`. The make targets wrap `docker compose -f tools/compose/compose.yml …` and export `config.env`. Compose project name is `ai-assistant`.
- CI publishes the smoke-tested image (linux/amd64) to `ghcr.io/slapshin/devops-assistant`: `:latest`, `:main` and `:sha-<short>` on every push to `main`; `:<version>` and `:<major>.<minor>` on a `v*` tag (e.g. `v1.2.3`). Pull requests build and test only. To run a published image instead of a local build, set `image:` in the compose file.
- The port is published on `127.0.0.1` only. Override it with `APP_PORT=18000 make up`.
- Metrics sources are configured per project in the UI. Inside the container `localhost` is the container itself: a project URL of `http://host.docker.internal:8428` reaches a metrics source, or an SSH tunnel such as `ssh -N -L 8428:localhost:8428 <monitoring-host>`, running on the Docker host. `extra_hosts: host-gateway` makes this work on Linux as well as Docker Desktop. Natively the same tunnel is `http://localhost:8428`.
- Reports live in the named volume `assistant-data` (mounted at `/data`). `make down` and `up --force-recreate` keep it. **Only `docker compose -f tools/compose/compose.yml down -v` deletes it.**

### Native

Requires uv 0.12+ (Python 3.14.7 is resolved from `backend/.python-version`) and Node.js 24.21 LTS.

```sh
make install                        # uv sync --locked; npm ci
cp config.env.template config.env  # optional: OPENAI_API_KEY, OPENAI_MODEL, …
(cd frontend && npm run build)      # the backend serves frontend/dist at /
cd backend && uv run python -m app  # http://127.0.0.1:8000 (APP_HOST/APP_PORT)
```

For development, run `make dev-backend` and `make dev-frontend`. The Vite dev server on :5173 proxies `/api` to :8000.

### Offline demo

`make demo` (Docker) or `DEMO_PROJECTS=true AI_PROVIDER=fake` (native) seeds one project per synthetic scenario when no project exists, and runs without a metrics source or an API key. Any project can use a `synthetic://<scenario>` URL. Scenarios are `healthy`, `incident`, `short-history` and `degraded`. Reports produced this way are labelled **Synthetic data** in the UI and carry `source.backend="synthetic"`.

## Configuration

All settings are environment variables, optionally read from `config.env` in the repository root (never commit it; the Makefile also exports it). Invalid values stop startup with exit code 2 and name the variable, for example `METRICS_URL: expected an http(s) URL such as http://localhost:8428 (got 'localhost:8428')`. Secrets are never printed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEMO_PROJECTS` | `false` | Seed synthetic demo projects when none exist. |
| `METRICS_URL`, `METRICS_BEARER_TOKEN`, `METRICS_BASIC_AUTH_*`, `METRICS_TLS_VERIFY` | unset (compose: `METRICS_URL=http://host.docker.internal:8428`) | **Deprecated.** Read once when upgrading from a pre-project release (see below), then ignored. |
| `AI_PROVIDER` | `openai` | `openai`, `none`, or `fake` (deterministic template, no network). |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | unset | The model must support Structured Outputs on the Responses API. Check it with `cd backend && uv run python -m scripts.check_openai [--structured]` (`--structured` makes one small paid call). |
| `DATA_DIR` | `./data` (native), `/data` (image) | SQLite database `assistant.sqlite3` and `secret.key`. |
| `SECRET_KEY` | unset | Encrypts project credentials stored in the database. When unset, a key is generated once into `DATA_DIR/secret.key` (mode 0600). Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` (image: `0.0.0.0` inside the container, published on 127.0.0.1) | Listen address. |
| `UI_STATIC_DIR` | `../frontend/dist` if built (image: `/app/static`) | Built UI served at `/`. |
| `DETECTOR_CONFIG_FILE` | unset | JSON file overriding detector defaults (see below). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

Without AI, numerical reports are complete. The explanation status is `disabled` (with `AI_PROVIDER=none`) or `not_configured` (when the key or model is missing).

## Data sources

Each project has at most one source per kind, configured in the UI:

- **Prometheus-compatible** (VictoriaMetrics, Prometheus): URL, TLS verification, none/bearer/basic auth. It needs at least one label matcher, which every query carries.
- **Cloudflare**: zone ID (dashboard → zone → Overview), optional hostnames, and an API token. Create the token under *My Profile → API Tokens → Custom token* with the permission **Zone → Analytics → Read** for that zone only; optionally add **Zone → Zone → Read** so findings show the zone's domain instead of its ID (without it the domain lookup is skipped and nothing else changes). Timing quantiles (TTFB, origin response time) need a Pro plan or higher; on other plans those signals are reported as unsupported. `synthetic://<scenario>` as the API URL serves demo data without a token.

- **Sentry**: the organization slug, up to 10 project slugs (as in the project URLs; each is analysed separately), an optional environment, optional tag filters (`key = value`, all must match, applied to every project), and an auth token with the scopes **org:read** and **project:read** (*User settings → Personal Tokens*, or an internal integration under *Settings → Custom Integrations*; organization tokens for CI cannot read events). EU organizations use `https://de.sentry.io` as the Sentry URL; self-hosted Sentry needs the `events-timeseries` endpoint (25.x or later; transactions are then read from the classic transactions dataset automatically). Transaction signals need tracing; without transactions in the last 24 h they are reported as unsupported. `synthetic://<scenario>` serves demo data without a token.

- **Wazuh**: the **Wazuh indexer** URL (OpenSearch API, `https://<indexer>:9200` by default; not the server API on port 55000), the alerts index pattern (default `wazuh-alerts-4.x-*`), the agents to analyse by name, by group and/or by agent label (`project = shop`; labels come from the agent's `<labels>` block or a group's shared `agent.conf`), and an indexer user and password. Groups are resolved from the `wazuh-monitoring-*` index, which the Wazuh dashboard fills only while `wazuh.monitoring.enabled` is on (the default); membership changes show up within 15 minutes. Give that user a role that can only read the alerts indices (and the monitoring indices when groups are used), e.g. in the indexer security plugin: a role with index patterns `wazuh-alerts-*` and `wazuh-monitoring-*` and the action group `read`, mapped to the user. The indexer uses a self-signed certificate by default: either make its CA trusted in the container or turn off TLS verification for the source. From Docker, the indexer must be reachable from the container (see Docker networking above). At most 50 agents are analysed per source. `synthetic://<scenario>` serves demo data without credentials.

Tokens and passwords are encrypted at rest and never returned by the API. When one source of a project fails (e.g. a revoked Cloudflare token), the other sources are still analysed and the report is `partial`.

## Supported metrics and history

The catalog (`docs/metrics-catalog.md`) covers:

- node exporter: CPU, iowait, memory, PSI, filesystem/inodes, disk, network
- cAdvisor: container CPU and memory, OOM; memory-vs-limit and throttling only when limits or CFS metrics exist
- Docker Swarm: task failures and replica shortfall, used as restart proxies
- OpenTelemetry HTTP and RPC: traffic, 5xx and RPC failures, 404 and other 4xx; p95/p99 latency from histograms, mean only as a fallback

Capabilities are discovered for each project at run time (the project form's **Test connection** shows them before saving). Anything missing is reported as *unsupported* or *insufficient data*, never as healthy.

For history:

- The latest-day comparison needs up to **15 days**.
- All 14 trend days with a full 14-day baseline need **28 days**.
- With less history, fewer baseline days are used and confidence drops. With fewer than 3 adequate baseline days, only absolute heuristics run.
- The verified source keeps 4 weeks (`-retentionPeriod=4w`), so the oldest trend day may have a slightly short baseline.

Thresholds are provisional diagnostic heuristics (`docs/DECISIONS.md` §5, `docs/detection.md`), not SLOs.

### Detector overrides

```json
{
  "z_threshold": 5.0,
  "signals": {
    "cpu_utilization": { "min_abs_effect": 0.2, "abs_floor": 0.01, "absolute_high": 0.95 }
  }
}
```

Top-level fields replace the defaults. Each `signals` entry replaces that signal's thresholds entirely, so include `abs_floor`. Any change produces a new `config_hash`, shown in the report header. Reports with different hashes are not directly comparable.

## Runtime budgets

| Budget | Value |
| --- | --- |
| Jobs | 1 running, 4 queued (`429 queue_full` beyond that); 10 min per job |
| Source requests | 30 s timeout (the source limit is also 30 s); 4 concurrent; 1 retry for transient errors; range queries in ≤ 7-day chunks; 64 MiB per response |
| Series | 500 per query and 5,000 per job; the excess is disclosed as `series_truncated` |
| AI | at most 20 findings and 40,000 characters of input, 2,000 output tokens, 60 s timeout |
| Report | 20 MB uncompressed; beyond that, secondary evidence and then episode lists are trimmed and disclosed (`evidence_dropped`) |

Typical measured figures on a real deployment (paas/production, 360 series): ≈ 40 s per analysis, a report of about 210 KB, about 60 KB stored compressed.

## Persistence, startup and restart

- Startup applies Alembic migrations forward only. It never drops tables or deletes reports.
- Report snapshots are immutable and reopen without the metrics source, even after the source's retention has passed.
- On startup, jobs that were queued or running when the process stopped are marked `failed` with `interrupted_by_restart`. They are **not** resumed; use *Run again*.
- Shutdown (`make down` or Ctrl-C) cancels running work. The next start records it as interrupted.
- Scheduled reports (a project's *Schedule* setting) run inside the app process, so they only run while it is up. A run missed by up to 6 hours (for example after a restart) still happens, analysing the 24 h up to its scheduled time; older missed runs are skipped and logged (`app.scheduler`). The scheduler never runs more than the job queue allows: with a full queue the run is retried every 30 s.
- Report retention (a project's *Report retention* setting) keeps only the newest N reports: after each analysis, and when the project is saved, older analyses of that project (including failed or cancelled ones older than the oldest kept report) are deleted with their reports. Deleted rows free space inside SQLite for reuse; the file itself only shrinks after `VACUUM`. Take a backup (`make backup`) before lowering N if old reports matter.
- Reports are **never** deleted automatically. Deleting a project deletes its analyses and reports (after confirmation in the UI).

### Upgrading from a pre-project release

Migration 0003 runs once at startup. It creates one project per `(project, env)` pair found in saved analyses, named `<project> / <env>` with matchers `project=<project>, env=<env>`, attaches those analyses to it, and rewrites the saved snapshots to report schema 2.0. All reports stay readable. On the same start, the deprecated `METRICS_*` settings (if set) become the metrics source of those projects, with their credentials encrypted in the database. The log says how many projects were updated. Afterwards the `METRICS_*` settings can be removed. If they were not set, the migrated projects have no source until you add one in the UI. Back up the database first (`make backup` / `make docker-backup`).

### Backup and cleanup

```sh
# native
cd backend && uv run python -m app.maintenance backup ../backups/assistant-$(date +%F).sqlite3
uv run python -m app.maintenance prune --older-than 90          # dry run: lists what would go
uv run python -m app.maintenance prune --older-than 90 --yes    # deletes finished analyses

# docker
docker compose -f tools/compose/compose.yml exec assistant /app/backend/.venv/bin/python -m app.maintenance backup /data/backup.sqlite3
docker compose -f tools/compose/compose.yml cp assistant:/data/backup.sqlite3 ./backup.sqlite3
```

`backup` uses SQLite's online backup, which is safe while the app runs, and refuses to overwrite an existing file. It does **not** include `DATA_DIR/secret.key`: back that file up separately (or set `SECRET_KEY`), because project credentials in the database cannot be decrypted without it. With a lost key the app still starts; affected projects show unreadable credentials until they are re-entered. `prune` never touches queued or running jobs, and deletes only when `--yes` is given.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Project shows *Unreachable* (Test connection: `reachable: false`) | Nothing listens at the project's URL. Natively, check the tunnel (`curl http://localhost:8428/api/v1/status/buildinfo`). In Docker, use `host.docker.internal` rather than `localhost`, and make sure the tunnel runs on the host. |
| Project shows *Auth failed* | The source rejected the stored token or password; edit the project and re-enter it. |
| Project shows *No matching series* | The source is reachable but no series carry all of the project's labels right now. Check the matcher names and values. |
| Project shows *Credentials unreadable* | `DATA_DIR/secret.key` (or `SECRET_KEY`) changed or was lost. Restore it, or re-enter the credentials. |
| Job `failed` / `metrics_source_unavailable` | The source went away during the run. Retry; saved reports are unaffected. |
| Report `partial`, a family shows *Source error* | Individual queries timed out or were truncated; each one is listed under *Omitted from this report*. Large scopes may need a quieter time or tighter budgets. |
| AI `not_configured` | Set `OPENAI_API_KEY` and `OPENAI_MODEL`, or use `AI_PROVIDER=none`. Numerical results are unaffected. |
| AI `failed (…)` | A provider problem (timeout, rate limit, refusal, invalid output). Findings and evidence are still complete. |
| `429 queue_full` | Wait for the running and queued analyses (up to 5 in total). |
| Container stays `unhealthy` | Check `make logs`: usually an invalid configuration (exit code 2) or an unwritable `/data`. |

## Verification record (T009, 2026-09-30)

- `docker build` succeeds: image `devops-ai-assistant:local`, 425 MB, Python 3.14.7, SQLite 3.46.1, runs as uid 10001. The health check reports `healthy`.
- With `METRICS_URL=synthetic://incident AI_PROVIDER=fake`, a report was produced. After `docker compose down` and `up --force-recreate` the same report was byte-identical (MD5). The UI deep link `/reports/x/trends` is served as HTML.
- With `METRICS_URL=http://host.docker.internal:8428` through the SSH tunnel, 31 projects were discovered and a live paas/production analysis completed in 40 s with 10 findings.
- `METRICS_URL=localhost:8428` exits with code 2 and the message above. An unreachable source (`:9`) returns health `reachable: false` and a 503 `metrics_source_unavailable` from discovery.
- The native run (`uv run python -m app`, tunnel on `localhost:8428`) was verified in T007.
