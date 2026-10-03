# T012 — Project-scoped analyses, reports, and legacy migration

Dependencies: T011. Status: see [task index](README.md).

## Outcome

Every analysis job and report belongs to a project. Collection uses that project's own source and matchers instead of the global `METRICS_URL` and `project`/`env` labels. Existing data is migrated automatically, and the global `METRICS_*` settings are retired.

## Ownership

`app/domain/common.py` (`Scope`), `app/metrics/promql.py`, `app/metrics/source.py`, `app/metrics/synthetic.py`, `app/service.py`, `app/jobs.py`, `app/main.py`, `app/container.py`, `app/settings.py`, `app/storage/`, `app/ai/prompt.py`, `app/api/routes.py`, migration `0003`, docs.

## Work

### Scope becomes the project's matchers

- `Scope {project_id, project_name, matchers: list[LabelMatcher]}` replaces `{project, env}`.
- `promql.scope_matchers` renders all matchers in sorted order. `assert_scoped` requires **every** matcher in every selector block. This keeps the invariant that every query, including ratio operands, is scoped.
- The synthetic source accepts any matcher set. Scenario data is generated per project.
- The AI prompt receives the project name and matchers (sanitised as today) instead of project/env.
- Numerical detection does not change, so `DETECTOR_VERSION` stays the same. Bump `REPORT_SCHEMA_VERSION`. The repository upgrades old snapshots at read time, mapping `{project, env}` to the project's matchers, so v1 reports stay readable.

### Per-project metrics sources

- Replace the single `Services.source` with a `SourceFactory` that builds the `MetricsSource` for a project. It decrypts the secrets, picks `PrometheusMetricsSource` or `SyntheticMetricsSource` by URL scheme, and caches clients by `(project_id, updated_at)`. Stale clients are closed when a project changes or is deleted.
- `AnalysisPipeline` receives its source per request. It stays framework-independent so CLI and scheduled use remain possible.
- A project without a Prometheus source, or with unreadable credentials, rejects submission with 409 `source_not_configured` or `credentials_unreadable`.

### Jobs and reports

- `analysis_jobs.project_id` gets a FK to `projects` with `ON DELETE CASCADE`, and `reports` cascades from jobs. Index `(project_id, analysis_id)` replaces `ix_jobs_scope`.
- `AnalysisSubmission {project_id, end_time?}`. Duplicate-active detection is keyed by `project_id`. `GET /analyses?project_id=` replaces the `project`+`env` filter.
- To avoid loading large reports on the list page, store a small `summary` JSON on the job when the report is saved: finding counts plus the 14-day daily anomaly counts.
- `ProjectSummary` (from T011) gains `latest_analysis: AnalysisJob | None`, `active_analysis: AnalysisJob | None`, and `trend: [{date, count}] | None` from that summary.
- Deleting a project removes its jobs and reports in one transaction. It is rejected while a job is queued or running.

### Migration of existing data and settings

- Migration `0003` creates one project per distinct `(project, env)` in `analysis_jobs`. The project is named `"<project> / <env>"` with matchers `project=<project>, env=<env>`. It sets `project_id` on those jobs, then makes the column NOT NULL and drops `project`/`env`.
- Secrets cannot be encrypted inside Alembic. Instead, a one-time startup import runs if the deprecated `METRICS_URL`/`METRICS_*` variables are set: it attaches them as the Prometheus source of every project that has no source yet, and logs a warning to remove them from `config.env`. With no jobs and `METRICS_URL` set, it creates nothing. The user creates projects in the UI.
- Remove `METRICS_*` from `Settings`, keeping only the import shim, and from `config.env.template`. `RuntimeConfig.metrics_source` is removed. `/api/health` reports database status only, because source health is now per project.
- Demo: `make demo` sets `DEMO_PROJECTS=1`, which seeds one `synthetic://<scenario>` project per scenario when there are no projects.

## Acceptance

- [x] Every generated query for a project contains all of its matchers (property test over the catalog with 1–3 matchers, including values with quotes and backslashes).
- [x] Two projects on different sources and URLs run, store, and list independently. No cache or report is shared between them.
- [x] Upgrading a database copy from the current release produces correct projects, keeps all old reports readable, and imports the legacy source once.
- [x] Hard delete removes the project, jobs, and reports and is refused while a job is active.
- [x] Interrupted-job handling, partial/exclusion semantics, and AI-independence still hold, with existing tests adapted.
- [x] `make check`, `docker build`, and `make demo` pass. Docs are updated: DECISIONS (API/config), ARCHITECTURE, OPERATIONS (backing up `secret.key` with the DB, migration notes), metrics-catalog (scope rule), AGENTS.md.

## Completion record

- Completed date: 2026-10-03
- Actual changed files and artifacts:
  - new: `app/metrics/factory.py` (`ProjectSources`, `connect`), `app/bootstrap.py` (legacy import, demo seeding), `migrations/versions/0003_project_scoped_jobs.py`, `tests/test_project_analyses.py`, `tests/helpers.py`, `tests/conftest.py`
  - domain: `Scope {project_id, project_name, matchers}` with `LabelMatcher` moved to `common.py`; `REPORT_SCHEMA_VERSION` 2.0; `SourceProvider`; `AnalysisSubmission {project_id}`; `AnalysisJob.daily_episodes`; `ProjectSummary`; error codes `project_busy` and `source_not_configured` added, `env_not_found` removed; `SourceStatus`/discovery contracts removed
  - metrics: `promql` requires every matcher; label discovery removed from `PrometheusMetricsSource`/`SyntheticMetricsSource`; `probe.py` adds capability families
  - `service.py`, `jobs.py`, `main.py`, `container.py`, `settings.py` (deprecated `METRICS_*`, `DEMO_PROJECTS`), `storage/db.py` (FK-safe migrate), `storage/repository.py`, `storage/projects.py`, `api/routes.py`, `api/projects.py`, `ai/prompt.py`, `analysis/engine.py`, `maintenance.py`
  - `scripts/export_schemas.py` now builds the app in a temporary data dir. Before this, `make contracts` migrated the developer's `backend/data` database in place.
  - minimal frontend switch to `project_id` (`StartView.vue`, `JobView.vue`, `ReportLayout.vue`, `api/*`, `App.test.ts`); T013 replaces the start page
  - `Makefile` (`make demo`), `tools/compose/compose.yml`, `config.env.template`, `README.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/OPERATIONS.md`, `docs/metrics-catalog.md`, `docs/contracts.md`; regenerated contracts, schema types and fixtures
- Commands/checks and results:
  - `make check` passes (354 backend tests passed, 2 skipped; 19 frontend tests; build).
  - `docker build` passes. The packaged image was run with `DEMO_PROJECTS=true AI_PROVIDER=fake` in a throwaway container: 4 demo projects were seeded, an analysis of `Demo: incident` completed, and `/api/projects/{id}/health` returned the capability families.
  - Real upgrade: the developer database `backend/data/assistant.sqlite3` (7 jobs, 7 reports, pairs `paas/production` and `pw-com/production`) was migrated to 0003. A copy served all 7 reports through the API, and the legacy import attached the source to both projects. `PRAGMA foreign_key_check` is clean.
- Decisions or dependency changes:
  - Migration 0003 converts the saved snapshots itself (to schema 2.0), so reports are not upgraded at read time.
  - Every analysis opens its own client from the stored project and closes it afterwards. No client is cached across jobs, so edits and deletes never affect a running job.
  - The analysis key (and therefore the derived IDs) is now based on the project's matchers instead of project/env.
  - `docker-compose` still passes `METRICS_URL` (default `http://host.docker.internal:8428`) only for the one-time import into Docker volumes from the pre-project release.
- Remaining limitations or blockers: the existing Docker volume `ai-assistant_assistant-data` was not upgraded here. It migrates on the next `make up`; back it up first with `make docker-backup`.
