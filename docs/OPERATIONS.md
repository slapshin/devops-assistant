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
- The port is published on `127.0.0.1` only. Override it with `APP_PORT=18000 make up`.
- Inside the container `localhost` is the container itself. The default `METRICS_URL=http://host.docker.internal:8428` reaches a metrics source, or an SSH tunnel such as `ssh -N -L 8428:localhost:8428 <monitoring-host>`, running on the Docker host. `extra_hosts: host-gateway` makes this work on Linux as well as Docker Desktop.
- To point at a network address instead: `METRICS_URL=http://vm.internal:8428 make up`. A `METRICS_URL` set in `config.env` applies to Docker too, so leave it unset there if it points at `localhost`.
- Reports live in the named volume `assistant-data` (mounted at `/data`). `make down` and `up --force-recreate` keep it. **Only `docker compose -f tools/compose/compose.yml down -v` deletes it.**

### Native

Requires uv 0.12+ (Python 3.14.7 is resolved from `backend/.python-version`) and Node.js 24.21 LTS.

```sh
make install                        # uv sync --locked; npm ci
cp config.env.template config.env  # METRICS_URL defaults to http://localhost:8428
(cd frontend && npm run build)      # the backend serves frontend/dist at /
cd backend && uv run python -m app  # http://127.0.0.1:8000 (APP_HOST/APP_PORT)
```

For development, run `make dev-backend` and `make dev-frontend`. The Vite dev server on :5173 proxies `/api` to :8000.

### Offline demo

`METRICS_URL=synthetic://incident AI_PROVIDER=fake` runs without a metrics source or an API key. Scenarios are `healthy`, `incident`, `short-history` and `degraded`. Reports produced this way are labelled **Synthetic data** in the UI and carry `source.backend="synthetic"`.

## Configuration

All settings are environment variables, optionally read from `config.env` in the repository root (never commit it; the Makefile also exports it). Invalid values stop startup with exit code 2 and name the variable, for example `METRICS_URL: expected an http(s) URL such as http://localhost:8428 (got 'localhost:8428')`. Secrets are never printed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `METRICS_URL` | `http://localhost:8428` (native), `http://host.docker.internal:8428` (compose) | Prometheus-compatible base URL; a path prefix is kept. `synthetic://<scenario>` selects demo data. |
| `METRICS_BEARER_TOKEN` or `METRICS_BASIC_AUTH_USER` + `METRICS_BASIC_AUTH_PASSWORD` | unset | Source credentials. They stay server-side and are never logged or stored in reports. |
| `METRICS_TLS_VERIFY` | `true` | TLS verification for https sources. |
| `AI_PROVIDER` | `openai` | `openai`, `none`, or `fake` (deterministic template, no network). |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | unset | The model must support Structured Outputs on the Responses API. Check it with `cd backend && uv run python -m scripts.check_openai [--structured]` (`--structured` makes one small paid call). |
| `DATA_DIR` | `./data` (native), `/data` (image) | SQLite database `assistant.sqlite3` and `secret.key`. |
| `SECRET_KEY` | unset | Encrypts project credentials stored in the database. When unset, a key is generated once into `DATA_DIR/secret.key` (mode 0600). Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` (image: `0.0.0.0` inside the container, published on 127.0.0.1) | Listen address. |
| `UI_STATIC_DIR` | `../frontend/dist` if built (image: `/app/static`) | Built UI served at `/`. |
| `DETECTOR_CONFIG_FILE` | unset | JSON file overriding detector defaults (see below). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

Without AI, numerical reports are complete. The explanation status is `disabled` (with `AI_PROVIDER=none`) or `not_configured` (when the key or model is missing).

## Supported metrics and history

The catalog (`docs/metrics-catalog.md`) covers:

- node exporter: CPU, iowait, memory, PSI, filesystem/inodes, disk, network
- cAdvisor: container CPU and memory, OOM; memory-vs-limit and throttling only when limits or CFS metrics exist
- Docker Swarm: task failures and replica shortfall, used as restart proxies
- OpenTelemetry HTTP and RPC: traffic, 5xx and RPC failures, 404 and other 4xx; p95/p99 latency from histograms, mean only as a fallback

Capabilities are discovered for each project/env at run time. Anything missing is reported as *unsupported* or *insufficient data*, never as healthy.

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
- Reports are **never** deleted automatically.

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
| Start page: "Metrics source unreachable"; `/api/health` shows `reachable: false` | Nothing listens at `METRICS_URL`. Natively, check the tunnel (`curl $METRICS_URL/api/v1/status/buildinfo`). In Docker, use `host.docker.internal` rather than `localhost`, and make sure the tunnel runs on the host. |
| "No series with a project label were found" | The source is reachable but has no `project`/`env` labels in the last 28 days. |
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
