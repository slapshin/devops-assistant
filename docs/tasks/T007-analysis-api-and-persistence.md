# T007 — Analysis API, jobs, and report persistence

Dependencies: T004, T005, T006. Status: see [task index](README.md).

## Outcome

Users can submit, track, cancel, and retrieve a complete analysis whose evidence survives an application restart.

## Ownership

`backend/app/api/`, orchestration, storage, and API/integration tests.

## Work

- Expose project discovery, project-scoped environment discovery, analysis submission, job status, report retrieval, and job cancellation.
- Proposed routes: `GET /api/projects`, `GET /api/projects/{project}/envs`, `POST /api/analyses`, `GET /api/analyses/{id}`, `GET /api/analyses/{id}/report`, `DELETE /api/analyses/{id}` for cancellation.
- Run analysis as a bounded background job. Persist status and completed/partial reports; expose stages for collection, detection, and explanation.
- Define queued/running/completed/partial/failed/cancelled behavior, duplicate submission policy, queue limits, and interrupted-job recovery after restart.
- Separate source errors, insufficient data, unsupported signals, and AI failure. Capture one end time for the whole job.
- Reuse the application service as the future CLI/reporting entry point. Keep secrets server-side and make logs useful without exposing credentials.
- Store sufficient versioned evidence/chart data with each report to reopen it independently of source retention.
- Add explicit persistence migrations and define report-retention behavior under the T001 storage budgets. Do not erase reports on startup.

## Acceptance

- [x] Project/env discovery, analysis submission, progress, cancellation, and report retrieval follow the frozen contract.
- [x] Collection, numerical analysis, and optional AI use one immutable scope/end time/configuration version.
- [x] Job transitions, duplicate submissions, queue limits, cancellation, and restart recovery behave as documented.
- [x] Completed/partial report data and evidence survive restart in real SQLite storage.
- [x] Source failure, insufficient data, unsupported metrics, and AI failure remain distinct.
- [x] The application service is reusable by future CLI and scheduling adapters.

## Verification

Use the real SQLite repository and deterministic source/provider adapters to exercise submission through retrieval, partial results, cancellation, duplicate requests, full queue, process interruption, and restart. Verify cross-scope isolation and a successful numerical report when AI fails.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `backend/app/storage/{db,repository}.py` and `backend/migrations/` (Alembic `0001_initial`, upgrade-only at startup)
  - `backend/app/service.py` (`AnalysisPipeline`, the framework-independent entry point, with the report size budget)
  - `backend/app/jobs.py` (`JobRunner`)
  - `backend/app/container.py`, `backend/app/main.py` (lifespan wiring, `synthetic://` source), `backend/app/api/routes.py` (all routes implemented)
  - `backend/app/domain/interfaces.py` (repository protocol), `app/settings.py`
  - `backend/tests/test_api.py` (rewritten as integration tests)
  - `docs/contracts.md`, `docs/DECISIONS.md` §4
- Commands/checks and results:
  - `make check` passes (266 backend tests passed, 2 skipped; ruff and mypy strict clean; 7 frontend tests; build).
  - The integration tests use real SQLite in `tmp_path`, `TestClient` with lifespan, and deterministic synthetic/gated/flaky/down sources. They cover:
    - discovery and 404s; end-time validation and flooring
    - submit → progress → report with valid explanation references
    - duplicate submissions (200 plus the same ID); `report_not_ready` (409); queue full (429 with `Retry-After`)
    - cancelling running and queued jobs; 409 on terminal jobs
    - AI failure and AI disabled keeping numerical reports; source exclusions giving `partial`; unreachable source giving `failed/metrics_source_unavailable`
    - restart: a completed report stays identical and a running job becomes `failed/interrupted_by_restart`
    - scope isolation and cursor pagination; tampered schema giving `schema_unsupported`
    - the pipeline used without FastAPI; report size budget disclosure
  - Live end-to-end (tunnel, read-only): uvicorn with `METRICS_URL=http://localhost:8428` and `AI_PROVIDER=fake`. paas/production completed in 43 s with 10 findings and a stable trend; the report was 214 KB. After restarting the server the report bytes were identical (MD5).
- Decisions or dependency changes:
  - Cancelled jobs have no error.
  - `schema_unsupported` is returned as a 404 problem.
  - The synthetic demo source is selected by `METRICS_URL=synthetic://…`.
  - Report budget handling first drops secondary evidence, then truncates episode lists, and discloses both as `evidence_dropped`.
  - No new dependencies.
- Remaining limitations or blockers:
  - Jobs are not resumed after a restart (by design).
  - The fake AI provider was used live; OpenAI remains unverified (see T006).
  - Single process only; no authentication (local exposure).
- Next ready task: T008.
