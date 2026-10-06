# T017 — Sentry source: application errors and transactions

Dependencies: T016. Status: see [task index](README.md).

## Outcome

A project can include up to 10 Sentry projects of one organization, each analysed as its own entity, optionally narrowed to one environment and to tag filters applied to every project. Analyses then cover its application errors (error events, unhandled errors, users hitting errors) and, when tracing is set up, its transactions (throughput, failed share, p95 duration). The same deterministic detector produces findings and trends from this data.

## Decisions (2026-10-06)

The request was "add new source sentry"; scope choices were made with the T015 pattern as the default and are recorded here for review.

- One Sentry source per project, covering up to 10 Sentry projects (owner request: "sentry can have many projects"), identified by organization and project slugs, optionally filtered to one environment and by `key = value` tag filters that apply to all of them (owner request). Each Sentry project is a separate entity so findings name it; probes query all projects together. The base URL defaults to `https://sentry.io` and can point at `https://de.sentry.io` or a self-hosted instance.
- Data comes from the documented `GET /api/0/organizations/{org}/events-timeseries/` endpoint and `GET /api/0/projects/{org}/{project}/`. The older `events-stats` endpoint is not used.
- Self-hosted Sentry 25.5.1 (the owner's version) was checked against its source: the response key is `timeseries` (docs: `timeSeries`), `meta.interval` is in ms, `project=` must be a numeric ID, and `dataset=spans` maps to the new span store. The client accepts both keys and units, filters by the numeric ID read from project details, and probes transactions in `spans` first, then the classic `transactions` dataset.
- Two new families keep Sentry signals apart from the Prometheus request families: `app_errors` and `app_performance`. New entity kind: `application`.
- New issues (first seen) and release health (crash-free sessions) are out of scope for the first version.

## Work

- Domain: `SourceKind.SENTRY`; `SentrySourceInput { organization, projects (1–10), environment?, tags (0–10 `SentryTag {key, value}`), auth_token (write-only), api_url }`; sources saved with a single `project` still load, read model `SentrySource` (`token_set`), `SentryConnection`.
- `app/sources/sentry/`: `api.py` (`SentryApi` protocol), `catalog.py` (signals and requests), `client.py` (bounded REST client: timeout, concurrency, size cap, one retry on 429/5xx honouring `Retry-After`; 401/403/404 → auth error; redirects reported with their target), `source.py` (capabilities over all projects, then per-project seven-day chunked collection from each project's creation date; untraced projects get no transaction series), `synthetic.py` (`healthy`, `incident`, `short-history`, `degraded`), `connect.py` (open and connection test).
- Detection: `rules/application.py` (`app_error_rate`, `app_unhandled_error_rate`, `app_error_users`, `app_transaction_rate`, `app_transaction_failure_ratio`, `app_duration_p95`), thresholds in `DetectorConfig` and `docs/DECISIONS.md` §5, a traffic spec in `derive.py`. `DETECTOR_VERSION` → `detectors-2026.10.8`.
- API: scenario validation, test connection and health dispatch on the kind. Demo projects get a synthetic Sentry source.
- UI: a Sentry fieldset (slugs, environment, token, advanced URL, Test Sentry connection), project page row, source icon, family labels. A Sentry test with zero events is not a health warning (a quiet app is legitimate).

## Live verification (when an organization and token are available)

- `events-timeseries` accepts repeated `yAxis`, `interval=5m`, `project=<id>` and `environment=<name>` (confirmed in the 25.5.1 source, not yet against a live instance).
- `spans` with `is_transaction:true` gives transaction counts, `failure_rate()` and `p95/p99(span.duration)` in milliseconds; empty duration buckets come back as `null` or `0` (both are dropped when there were no transactions).
- Whether `sentry.io` serves EU organizations or redirects to `de.sentry.io`.
- Sentry's rate limits for one analysis (3 × 28 requests, 4 concurrent).

## Acceptance

- [x] A Sentry source can be created, edited (token kept when omitted), cloned, and tested. The token is encrypted at rest and never returned or logged.
- [x] Against a mocked transport, the client sends the documented parameters, converts failures and durations, rejects non-5-minute buckets, and maps auth/redirect/rate-limit/server errors without echoing the token.
- [x] Collection starts at the project's creation, chunks by day, marks failed chunks as unknown with exclusions, and marks transaction signals unsupported without tracing.
- [x] `synthetic://incident` yields error, unhandled, user, failure-share and duration findings; `healthy` yields none; `degraded` is `partial`.
- [x] A Sentry failure in a mixed project makes the report `partial`, not `failed`.
- [x] Several projects are analysed as separate entities; tag filters are quoted search terms on every request; sources saved with one `project` still load.
- [x] `make contracts`, `make fixtures`, and `make check` pass.

## Completion record

- Completed date: 2026-10-06
- Actual changed files and artifacts:
  - new: `backend/app/sources/sentry/` (`api.py`, `catalog.py`, `client.py`, `source.py`, `synthetic.py`, `connect.py`), `app/analysis/rules/application.py`, `tests/test_sentry.py`, fixtures `reports/report_sentry.json` (generated by the real pipeline) and `api/connection_test_sentry.json`
  - changed: `domain/common.py` (kind, families, entity kind), `domain/projects.py` (Sentry models), `domain/detector_config.py`, `analysis/derive.py`, `analysis/rules/__init__.py`, `storage/projects.py`, `sources/factory.py`, `api/projects.py`, `bootstrap.py`, `scripts/generate_fixtures.py` (shared `pipeline_report`); frontend `lib/projects.ts`, `lib/format.ts`, `ProjectFormView.vue`, `ProjectView.vue`, `ProjectsView.vue`, `SourceIcon.vue`, `App.test.ts`; regenerated contracts, `schema.d.ts` and fixtures (detector version bump)
  - docs: `DECISIONS.md` §5, `detection.md`, `metrics-catalog.md`, `OPERATIONS.md`, `UI_SPEC.md`, `ARCHITECTURE.md`, `AGENTS.md`
- Commands/checks and results: `make contracts`, `make fixtures`, `make check` pass.
- Remaining limitations: built without a live Sentry instance (see "Live verification"); compatibility with self-hosted 25.5.1 is checked against its source code only. Self-hosted Sentry older than 25.x (without `events-timeseries`) is not supported (no `events-stats` fallback). Transaction counts depend on the client's trace sample rate. No new-issue or release-health signals yet.
