# T002 — Application workspace and shared contracts

Dependencies: T001. Status: see [task index](README.md).

## Outcome

The backend and frontend share a reproducible workspace and versioned contracts without depending on live metrics or AI.

## Ownership

Project manifests/lockfiles, settings, `backend/app/domain/`, `docs/contracts.md`, and shared API fixtures.

## Work

- Establish the project layout and dependency/tooling choices.
- Implement and document the [shared contracts and interfaces](../ARCHITECTURE.md#shared-contracts), including a framework-independent analysis entry point.
- Define configuration validation and report schema/version policy.
- Provide shared fixtures: supplied labels, normal behavior, anomalies, missing data, partial history, and unavailable AI.
- Own dependency changes requested by other agents to avoid concurrent lockfile edits.
- Pin and verify the T001 runtime/dependency choices; record startup/build/check commands and configuration validation.
- Freeze exact routes, units, nullable fields, errors, report/evidence IDs, capability states, and schema versions in contract examples before downstream work.
- Produce runnable application foundations and repository checks, while keeping future task stubs visibly incomplete.

## Acceptance

- [x] Backend/frontend foundations start and their documented build/check commands work.
- [x] Shared contracts cover every type and interface in the architecture, including partial/error states.
- [x] Schema examples use the supplied label conventions and permit independent UI/analysis development.
- [x] Domain logic imports neither LLM SDK types nor web framework request types.
- [x] Configuration validation and exact runtime/dependency choices are reproducible.

## Verification

Run the foundation build/checks and validate representative JSON examples against the schema. Confirm configuration errors are actionable and no real OpenAI key or metrics connection is required for contract validation.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - Backend: `backend/` (`pyproject.toml`, `uv.lock`, `.python-version`, `app/settings.py`, `app/main.py`, `app/api/{routes,problems}.py`, `app/domain/{common,metrics,findings,explanation,report,jobs,detector_config,ids,interfaces}.py`, empty `metrics/analysis/ai/storage` packages, `scripts/{export_schemas,generate_fixtures}.py`, `tests/test_{contracts,settings,api}.py`).
  - Frontend: `frontend/` (`package.json`, `package-lock.json`, `.nvmrc`, Vite/TS/ESLint config, `src/api/{client.ts,schema.d.ts}`, router with the UI_SPEC routes, Start page showing `/api/config`, design tokens, Vitest tests, `scripts/check-api-types.mjs`).
  - Shared: `docs/contracts.md`, `docs/contracts/*.schema.json` + `openapi.json`, `fixtures/{reports,jobs,api,metrics}/*.json`.
  - Root: `Makefile`, `.gitignore`, `.env.example`, `README.md` (replaced the GitLab template).
  - Docs: `docs/DECISIONS.md` (T002 exceptions and verification).
- Commands/checks and results:
  - `make check` passes: ruff format/check, mypy strict (27 files), contract and fixture drift checks, and pytest (57 passed, 1 skipped for the schema-less capability manifest, which has its own test).
  - Frontend `npm run check` passes: API-type drift check, `tsc`, ESLint, Vitest (7 passed), and the Vite build.
  - In `node:24.21.0-trixie-slim`, `npm ci && npm run check` passes (Node v24.21.0).
  - `uvicorn app.main:create_app --factory` serves `/api/health` (200), and stub routes return `501 application/problem+json`.
  - `METRICS_URL=localhost:8428` exits 2 with `METRICS_URL: expected an http(s) URL such as http://localhost:8428 (got 'localhost:8428')`.
  - Fixtures validate against the exported JSON Schemas and round-trip through the models. No OpenAI key or metrics connection was used.
- Decisions or dependency changes:
  - openapi-typescript runs against TypeScript 6.0.3 via npm `overrides`, because its peer range is `^5.x`.
  - Added pytest-asyncio 1.4.0, jsonschema 4.26.0, and types-jsonschema as dev dependencies.
  - The frontend build image is `node:24.21.0-trixie-slim`.
  - Routes for T004/T007 return `501 not_implemented` (a foundation-only error code).
- Remaining limitations or blockers:
  - No CI pipeline file was added, because the runners on `gitlab.artworks.ai` are unknown; `make check` is the single entry point for one.
  - `/api/health` reports `database: not_initialized` and does not check metrics until T004/T007.
  - Fixture values are illustrative, not detector output.
  - Detector config file overrides (`DETECTOR_CONFIG`) are validated for existence but loaded by T005.
- Next ready task: T003 (read-only telemetry discovery); T005 is also ready (depends only on T002).
