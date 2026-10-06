# T014 — Multi-source foundation

Dependencies: T013. Status: see [task index](README.md).

## Outcome

A project can be analysed from more than one data source in a single run. The pipeline opens every configured source, merges their capabilities, series and exclusions, and one failing source no longer fails the whole job. Prometheus works exactly as before, and the code is ready for a second source kind (Cloudflare, T015) to be added as a union member plus an adapter.

## Owner decisions (2026-10-06)

- Data sources are a project-level concept. Prometheus is one kind; Cloudflare comes next, and Wazuh and Sentry later.
- Label matchers are needed only by Prometheus. A project without a Prometheus source (e.g. Cloudflare only) has no matchers.
- One source per kind per project (unchanged).

## Ownership

`backend/app/domain/{projects,common,report,metrics,interfaces}.py`, `backend/app/metrics/factory.py` (multi-source provider), `backend/app/service.py`, `backend/app/jobs.py`, `backend/app/scheduler.py`, `backend/app/api/{projects,routes}.py`, `backend/app/metrics/{probe,source,promql}.py` (guards only), frontend health/matchers handling, contracts/fixtures regeneration.

## Work

### Domain

- `ProjectInput.matchers` becomes optional (0–10 matchers). The validator requires at least one matcher when a Prometheus source is configured. `Scope.matchers` may be empty. Prometheus code refuses an empty scope (`ScopeViolation`), so a query can never run unscoped.
- `SourceInfo` gets `kind: SourceKind` (default `prometheus`). `AnalysisReport` gets `sources: list[SourceInfo]`. `source` becomes optional and holds the first source, so 2.0 reports still load. Report schema version becomes `2.1` (minor: stored 2.0 reports stay readable).
- `MetricCapability` gets `source: SourceKind` (default `prometheus`).
- `ConnectionTest` gets `kind: SourceKind` (default `prometheus`).
- `analysis_key` includes the project ID, so finding IDs of two projects with equal (or no) matchers never collide. Bump `DETECTOR_VERSION`.

### Sources

- `SourceProvider.open(scope)` yields `list[OpenedSource]` (kind + `MetricsSource`), one per configured source, all opened and closed together. With the test `override`, it yields one Prometheus source as today.
- `SourceNotConfigured` is raised only when a project has no source at all.
- `SqliteProjectRepository` exposes `connections(project_id)`: decrypted connections for every stored kind.

### Pipeline (`app/service.py`)

- Discovery and collection run per source. Capabilities, series, exclusions and mappings are merged. Progress messages name the source.
- A source that fails (`SourceError`) adds a `source_unavailable` exclusion naming the source kind (no secrets) and makes the report `partial`. If every source fails, the job fails as before (`metrics_source_unavailable`).

### API and scheduler

- `GET /api/projects/{id}/health` returns `list[ConnectionTest]`, one per source (empty when none is configured), each cached as before.
- The scheduler and `POST /api/analyses` require at least one source of any kind, not a Prometheus source.

### Frontend

- The health badge combines the per-source results (the worst one wins).
- The form allows an empty matcher list when no Prometheus URL is set.
- The report shows every source in `sources` (it falls back to `source` for old reports).

## Acceptance

- [x] Existing Prometheus analyses, tests and fixtures behave as before (apart from the new fields and finding IDs).
- [x] With two fake sources where one raises `SourceError`, the report is `partial`, contains the healthy source's findings, and lists a `source_unavailable` exclusion. With all sources failing, the job fails.
- [x] A project without a Prometheus source can be stored without matchers. A Prometheus source without matchers is rejected with `validation_error`.
- [x] A stored 2.0 report still loads.
- [x] `make contracts`, `make fixtures`, and `make check` pass.

## Completion record

- Completed date: 2026-10-06
- Actual changed files and artifacts:
  - domain: `common.py` (`SourceKind` moved here, `OptionalMatchers`, `SOURCE_FAMILIES`, `REPORT_SCHEMA_VERSION = "2.1"`), `projects.py` (optional matchers validated per source kind, `ConnectionTest.kind`, `SourceConnection`), `report.py` (`SourceInfo.kind`, `sources`, optional `source`, `all_sources`), `metrics.py` (`MetricCapability.source`), `interfaces.py` (`OpenedSource`, list-yielding `SourceProvider`, `SourceError`/`SourceErrorKind` moved from `metrics/client.py`), `detector_config.py` (`detectors-2026.10.6`)
  - `metrics/factory.py` (opens all sources with an `AsyncExitStack`), `storage/projects.py` (`connections`), `service.py` (`_gather`: per-source discovery/collection, `source_unavailable`), `analysis/engine.py` (project ID in `analysis_key`; coverage only for applicable families; failed-source families are `source_error`), `metrics/promql.py` (empty scope refused), `api/projects.py` (health is a list), `api/routes.py` and `scheduler.py` (any source kind suffices)
  - tests: new `tests/test_sources.py`; `helpers.py`, `test_projects.py`, imports in `test_api.py`/`test_metrics.py`
  - frontend: `api/queries.ts`, `api/client.ts`, `lib/projects.ts` (worst-of health, optional matchers, `reportSources`), `HealthBadge.vue`, `ReportLayout.vue`, `App.test.ts`
  - regenerated contracts, `schema.d.ts`, fixtures; docs: `ARCHITECTURE.md`, `contracts.md`, `detection.md`, `DECISIONS.md`, `AGENTS.md`
- Commands/checks and results: `make contracts`, `make fixtures`, `make check` pass.
- Remaining limitations: the report UI shows sources only through the synthetic badge; a visible sources list comes with T016.
