# AGENTS.md

## What this is

A local web app that analyses **projects**: each project selects its series with equality label matchers (e.g. `project="shop", env="prod"`) on its own Prometheus-compatible source (VictoriaMetrics in practice); more source kinds (Wazuh, Cloudflare, Sentry) are planned. It finds anomalies in the latest 24 h against up to 14 preceding days, builds a 14-day anomaly trend, and optionally adds AI explanations (OpenAI). Backend: Python 3.14 + FastAPI + SQLite (uv). Frontend: Vue 3 + TypeScript + Vite + ECharts (npm, Node 24 LTS). FastAPI serves the built frontend in production; a single Docker container runs everything.

## Commands

All from the repo root. `make help` lists every target. The Makefile includes and exports `config.env` (copy from `config.env.template` with `make config`; it is gitignored).

```sh
make install         # uv sync --locked + npm ci
make check           # full gate: backend + frontend checks (run before declaring work done)
make check-backend   # ruff format --check, ruff check, mypy (strict), schema/fixture drift, pytest
make check-frontend  # npm run check: API-type drift, vue-tsc, eslint (0 warnings), vitest, build
make dev-backend     # uvicorn with reload on :8000 (API docs at /api/docs)
make dev-frontend    # Vite on :5173, proxies /api to the backend
make serve           # build frontend, then run the app natively (python -m app)
make demo            # Docker with DEMO_PROJECTS=true AI_PROVIDER=fake (synthetic projects; no metrics/API key needed)
make up / down / logs / health
```

Single tests:

```sh
cd backend && uv run pytest tests/test_analysis.py::test_name -q
cd frontend && npx vitest run src/App.test.ts -t "test name"
```

Offline running without a metrics source: a project whose source URL is `synthetic://<scenario>` (scenarios in `backend/app/metrics/synthetic.py`: `healthy`, `incident`, `short-history`, `degraded`; `DEMO_PROJECTS=true` seeds one per scenario) and `AI_PROVIDER=fake|none`. Check an OpenAI model supports structured output: `cd backend && uv run python -m scripts.check_openai --structured`.

## Generated artefacts (drift-checked in CI)

Pydantic models in `backend/app/domain/` are the single source of truth for all contracts. After changing any domain model or API route:

1. `make contracts` — regenerates `docs/contracts/*.schema.json`, `docs/contracts/openapi.json`, and `frontend/src/api/schema.d.ts` (via openapi-typescript).
2. `make fixtures` — regenerates `fixtures/**` JSON (synthetic, sanitised) from the models.

`make check` fails if any of these are stale. Never hand-edit `schema.d.ts`, the schemas, or fixtures.

## Architecture

Flow: browser → API → `JobRunner` → `AnalysisPipeline` → metrics source → deterministic detection → optional AI explanation → report saved in SQLite → browser polls and renders.

- `app/main.py` — `create_app()` factory; `build_services()` wires `Services` (`app/container.py`): settings, `DetectorConfig`, `SqliteProjectRepository` (projects + encrypted source secrets, `app/storage/secrets.py`), `ProjectSources` (`app/metrics/factory.py`: opens a project's `PrometheusMetricsSource` or `SyntheticMetricsSource` per analysis), `ExplanationProvider`, `SqliteReportRepository`, `JobRunner`. Tests inject fakes through `create_app(settings, source, provider, limits)`; `source` replaces every project's source. `app/bootstrap.py` runs the one-time legacy `METRICS_*` import and demo seeding at startup.
- `app/domain/` — contracts and Protocol interfaces (`MetricsSource`, `SourceProvider`, `Detector`, `ExplanationProvider`, `ReportRepository`, `AnalysisService`, progress/cancellation). Other layers depend on these, not on each other.
- `app/service.py` — `AnalysisPipeline`, the framework-independent entry point (intended for future CLI/scheduled use too). Freezes one UTC end time `T` per run; stages: discovery → collection → detection → explanation.
- `app/scheduler.py` — `ReportScheduler`: background loop that submits each project's due scheduled analyses (`ReportSchedule` in `app/domain/schedule.py`) to the runner.
- `app/jobs.py` — bounded in-process runner (1 running, ≤4 queued, timeout, cancellation by task cancel). On startup, interrupted jobs are marked failed.
- `app/metrics/` — `client.py` (HTTP Prometheus API, auth, preserves URL path prefix), `catalog/` (scoped query catalog: `base.py` signal model + template builders; one module per source: `host`, `container`, `requests`, `proxy`, `postgres`, `mysql`, `redis`), `promql.py` (every selector carries all project matchers), `source.py` (capability discovery + bounded collection), `factory.py`, `probe.py` (connection test).
- `app/api/projects.py` — project CRUD, `test-connection`, cached per-project health.
- `app/analysis/` — `engine.py` (`RobustDetector`, trend summary), `baseline.py` (median/MAD), `detect.py`, `rules/` (`base.py` rule model + formatting; one module per category: `host`, `container`, `requests`, `proxy`, `database`), `derive.py`.
- `app/ai/` — `providers.py` (provider selection, `fake`), `prompt.py`, `validation.py` (validates model output references real finding IDs), `openai_adapter.py`.
- `app/storage/` — SQLAlchemy Core (no ORM) + Alembic migrations in `backend/migrations/`, run upgrade-only at startup. Reports are stored as versioned JSON snapshots (`REPORT_SCHEMA_VERSION`).
- `app/api/` — routes under `/api`; errors are RFC 9457 `application/problem+json` with a stable `code` (`problems.py`).
- `frontend/src/` — routes `/` (project list), `/analyses/:id` (job progress), `/reports/:id` with `findings`, `findings/:findingId`, `trends` children. Server state via TanStack Vue Query (no Pinia); API client typed from generated `schema.d.ts`; styling is plain CSS: tokens in `styles/tokens.css`, shared dashboard classes (panels, 24-column grid, chips) in `styles/base.css`; fonts are self-hosted IBM Plex via `@fontsource`.

## Invariants to preserve

- Only `app/ai/openai_adapter.py` may import `openai` (enforced by ruff `TID251`).
- Numerical findings never depend on AI. AI failure must not change a `completed` job to `failed`/`partial`; explanation status is tracked separately.
- Missing or partial data is never reported as healthy: insufficient coverage/baseline is surfaced as such, and source errors/truncation make a job `partial` with listed exclusions.
- Baselines use only data preceding the analysed bucket. Severity reflects magnitude/duration; confidence reflects data quality — keep them separate.
- HTTP 5xx and 4xx are analysed separately; `error_type` alone never makes a request a server failure. Latency requires histogram buckets; count-only data never yields latency.
- Changing detector behaviour means bumping `DETECTOR_VERSION` in `app/domain/detector_config.py`. Detector defaults and the numerical policy are specified in `docs/DECISIONS.md` §5.
- API conventions: `snake_case`, RFC 3339 UTC timestamps with `Z`, durations in integer seconds, UUIDv7 IDs.
- Dependency versions are pinned exactly; TypeScript stays on 6.0.x until typescript-eslint supports 7 (see `docs/DECISIONS.md` §1).

## Docs

`docs/DECISIONS.md` (versions, config, API/job conventions, numerical policy), `docs/ARCHITECTURE.md`, `docs/UI_SPEC.md`, `docs/OPERATIONS.md` (config, Docker networking, backups, troubleshooting), `docs/detection.md`, `docs/metrics-catalog.md`. Task history T001–T010 (all DONE) is in `docs/tasks/`; `docs/tasks/README.md` describes the task workflow if new tasks are added.
