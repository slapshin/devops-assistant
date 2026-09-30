# T009 — Local packaging and operation documentation

Dependencies: T007, T008. Status: see [task index](README.md).

## Outcome

A fresh checkout can run the packaged app with persistent reports and documented configuration.

## Ownership

Docker/build configuration, `.env.example`, root README, and `docs/OPERATIONS.md`.

## Work

- Package the backend and built UI for local execution using the runtime/dependency choices recorded in T001/T002.
- Persist SQLite reports in a volume and document non-destructive startup, shutdown, migrations, and restart behavior.
- Document native and Docker setup, required/optional settings, supported metrics, thresholds, history requirements, and troubleshooting.
- Preserve `METRICS_URL=http://localhost:8428` for native execution; explain container localhost and provide an appropriate configurable host-address/network option.
- Keep keys and metrics credentials server-side. Local exposure is the initial scope; do not publish a service or modify the metrics backend.
- Document source/query/runtime budgets, report storage limits, and a non-destructive method to preserve/copy saved reports.
- Replace the default GitLab README with accurate setup/use links once the application is available.

## Acceptance

- [x] Documented native and Docker startup work from a fresh checkout with reproducible dependencies/images.
- [x] Packaged UI/backend operate together with the configured metrics URL and optional AI settings.
- [x] Saved reports survive container recreation without deleting the volume.
- [x] Connectivity and invalid-configuration failures are understandable and documented.
- [x] Runtime, persistence, migrations, storage limits, and local exposure match the documentation.

## Verification

Start the packaged app in an isolated local setup, produce a synthetic report, recreate the application container while preserving its volume, and reopen the report. Check native and container URL configuration. Record exact commands and environment limitations; external deployment is not required.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `Dockerfile` (Node 24.21 build stage → Python 3.14.7-slim runtime, uv 0.12.19, non-root uid 10001, `/data` volume, healthcheck), `.dockerignore`, `compose.yaml` (127.0.0.1 only, `host.docker.internal:host-gateway`, named volume)
  - `backend/app/{__main__,static,maintenance}.py`
  - `backend/app/main.py` (UI mount; `DETECTOR_CONFIG` JSON overrides), `backend/app/settings.py` (`STATIC_DIR`)
  - `backend/tests/test_operations.py`
  - `docs/OPERATIONS.md` (new), `README.md` (replaced), `.env.example`, `Makefile` (build/serve/docker/backup targets), `.gitignore`, `docs/DECISIONS.md` §2/§6
- Commands/checks and results:
  - `make check` passes (269 backend tests passed, 2 skipped; 16 frontend tests; build).
  - `docker build -t devops-ai-assistant:local .` succeeds (425 MB; Python 3.14.7; SQLite 3.46.1; uid 10001; healthy).
  - Compose run with `METRICS_URL=synthetic://incident`: report produced; after `down` and `up --force-recreate` it was byte-identical (MD5); SPA deep link served.
  - Compose run with `METRICS_URL=http://host.docker.internal:8428` through the SSH tunnel: 31 projects; live paas/production analysis completed in 40 s with 10 findings.
  - An invalid `METRICS_URL` exits 2 with an actionable message. An unreachable source gives health `reachable:false` and a 503 `metrics_source_unavailable`.
  - Native `uv run python -m app` serves `/`, deep links, assets and the API.
  - The verification volume was removed afterwards.
- Decisions or dependency changes:
  - `DETECTOR_CONFIG` is JSON only.
  - The backend serves the UI (a single container).
  - Maintenance is explicit only: backup never overwrites, and prune is a dry run unless `--yes`.
  - No new runtime dependencies.
- Remaining limitations or blockers:
  - The image is not published to a registry, and there is no CI pipeline file.
  - No authentication, so exposure is local only.
  - Docker was verified on Docker Desktop for macOS; `host-gateway` on Linux is documented but was not exercised here.
- Next ready task: T010.
