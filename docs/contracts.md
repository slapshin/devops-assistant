# Shared contracts (T002)

Status: frozen 2026-09-30 for downstream tasks T003–T008. To change a contract, edit the Pydantic model, run `make contracts fixtures`, update this page, and note the change in each affected task's completion record.

Source of truth: `backend/app/domain/`. Generated artifacts, drift-checked by `make check`:

| Artifact | Generator |
| --- | --- |
| `docs/contracts/*.schema.json` (JSON Schema, serialization mode) | `uv run python -m scripts.export_schemas` |
| `docs/contracts/openapi.json` | same script, from the FastAPI app |
| `frontend/src/api/schema.d.ts` | `npm run gen:api` (openapi-typescript) |
| `fixtures/**/*.json` | `uv run python -m scripts.generate_fixtures` |

## Types and interfaces

| Architecture contract | Model | Module |
| --- | --- | --- |
| `AnalysisRequest` | `AnalysisRequest` (+ HTTP body `AnalysisSubmission`) | `domain/report.py`, `domain/jobs.py` |
| `MetricSeries` | `MetricSeries` | `domain/metrics.py` |
| `MetricCapability` | `MetricCapability` | `domain/metrics.py` |
| `Finding` | `Finding` (+ `Evidence`, `SignalCoverage`) | `domain/findings.py` |
| `DailyTrend` | `DailyTrend` (+ `EpisodeSummary`) | `domain/findings.py` |
| `AnalysisReport` | `AnalysisReport` (+ `AnalysisWindows`, `SourceInfo`, `Exclusion`) | `domain/report.py` |
| `Explanation` | `Explanation`, `ExplanationResult`, `ExplanationInput` | `domain/explanation.py` |
| Job lifecycle / API bodies | `AnalysisJob`, `StageProgress`, `Problem`, `ProjectList`, `EnvList`, `RuntimeConfig`, `HealthResponse` | `domain/jobs.py` |
| Detector configuration | `DetectorConfig` (defaults = DECISIONS §5, `config_hash`) | `domain/detector_config.py` |

Interfaces (`domain/interfaces.py`). T004 added `DiscoveredValues` (values plus a `truncated` flag), `EntityMapping`/`MappingKind`, and `CollectionResult.mappings`, all additive: `MetricsSource` (T004), `Detector` (T005), `ExplanationProvider` (T006), `AnalysisService` (the framework-independent entry point, T007), `ReportRepository` (T007), and `ProgressReporter`/`CancellationToken`. The domain package imports no web framework, HTTP client, ORM, or LLM SDK. This is enforced by `tests/test_contracts.py` and a ruff banned-import rule for `openai`.

## Conventions

- **Field names** are `snake_case`. **Times** are RFC 3339 UTC with `Z` at second precision. Naive datetimes are rejected on input, and other offsets are normalised to UTC.
- **Windows** are half-open `[start, end)`. `AnalysisRequest.end_time` must be aligned to the 300 s step (the API floors submissions). `trends` always has exactly 14 buckets ordered `bucket_index` 0 (latest day, `[T-24h, T)`) to 13.
- **Series** are a regular grid: `values[i]` is at `start + i * step_seconds`, and `null` means no sample (never zero). `coverage` must equal the share of non-null values. `Evidence.expected/lower/upper` align index-for-index with `series.values`.
- **Units** (`Unit`): `ratio` (0–1; the UI renders it as a percent), `bytes`, `bytes_per_second`, `requests_per_second`, `per_second`, `seconds`, `count`.
- **Nullable fields** are nullable only where meaningful:
  - `Finding.expected`, `peak_score`, `baseline_days`, `baseline_mode` are null for absolute checks.
  - `Finding.threshold` is null for relative findings.
  - `DailyTrend.anomalous_share` and `peak_severity` are null when nothing was observed or there were no episodes.
  - `EpisodeSummary.finding_id` is null for earlier-day episodes.
  - `ExplanationResult.explanation` is null unless `status=succeeded`.
- **Severity** is `low | medium | high | critical`. **Confidence** is `low | medium | high`, with `confidence_reasons[{code, message}]` listing every lowering factor. The two are never combined.
- **Detection method**: `relative` needs `expected` and `peak_score`. `absolute` needs `threshold`, which is a diagnostic heuristic, never an SLO.

## Identifiers and ownership

| ID | Format | Owner | Derivation |
| --- | --- | --- | --- |
| `analysis_id` | UUIDv7 string | T007 (job creation) | `ids.new_analysis_id()` |
| `series_id` | `ser_` + 16 hex | T004 | hash of query + entity key |
| `finding_id` | `fnd_` + 16 hex | T005 | hash of analysis key, entity key, signal, start |
| `episode_id` | `eps_` + 16 hex | T005 | same inputs as findings |
| `evidence_id` | `evd_` + 16 hex | T005 | hash of finding ID + series ID |

The analysis key is `analysis_id|project|env|T|config_hash`. Report validation rejects any finding, evidence, related-finding, or explanation reference that does not resolve inside the same report.

## States

- **Capability** (`CapabilityStatus`): `supported | partial | unsupported | unverified`. `verified=true` only when the capability was observed in the live source. T003 produced the first verified manifest for paas/production (`fixtures/metrics/capabilities_paas_production_observed.json`). Other scopes are discovered at runtime.
- **Signal coverage** (`SignalStatus`): `anomalous | no_anomaly | insufficient_data | unsupported | source_error | not_evaluated`. The UI vocabulary is in UI_SPEC §5.
- **Trend bucket**: `ok | insufficient_baseline | insufficient_data | source_error`.
- **Job** (`JobState`): `queued | running | completed | partial | failed | cancelled`. **Stages**: `discovery, collection, detection, trends, explanation, saving`, each `pending | running | done | failed | skipped`.
- **Explanation**: `disabled | not_configured | pending | succeeded | failed | skipped_no_findings`, separate from the job state.
- **Report**: `completed | partial`. Failed and cancelled jobs have no report.

## Exclusion codes

`query_failed`, `query_timeout`, `series_truncated` (per-query or per-job series budget), `evidence_dropped` (report size budget), `label_cardinality` (discovery guard). Each has a `message`, and optionally a `family` or `finding_id`.

## Errors and routes

Routes and `ErrorCode` values are as listed in [DECISIONS §3](DECISIONS.md#3-api-conventions), and are visible in `docs/contracts/openapi.json`. Errors are `application/problem+json` with `{type, title, status, detail?, code, errors?}`. Validation errors list `errors[{field, message}]`, where `field` is a dotted location such as `body.env`.

`not_implemented` (501) is a **foundation-only** code. Routes whose behaviour belongs to T004/T007 return it until then, so the contract is visible but the feature is visibly incomplete.

## Schema versions

`AnalysisReport.schema_version = "1.0"`. Adding an optional field is a minor bump. Removing, renaming, or changing the meaning of a field is a major bump and needs a reader migration in T007. `DetectorConfig.version` (`detectors-2026.09.1`) and `config_hash` (the first 12 hex characters of the SHA-256 of the canonical config JSON) identify the calculation.

## Fixtures

All fixtures are synthetic and sanitised. They use the supplied label conventions (`project="paas"`, `env="production"`, node `job="node", instance="paas-production"`, HTTP `job="dispatcher-api"`, route `/api/v3/tasks/:task`, `http_response_status_code`, `error_type`) and contain no credentials. T = `2026-09-30T10:05:00Z`.

| File | Scenario |
| --- | --- |
| `reports/report_healthy.json` | Normal behaviour: no findings, full coverage except unsupported containers; AI `skipped_no_findings` |
| `reports/report_anomalies.json` | Sustained CPU (critical, relative, time-of-day baseline) plus a separate 404 increase (medium, with a scrape gap); 404 recurrences on trend days 3/5/9; AI `succeeded` with hypotheses |
| `reports/report_ai_failed.json` | The same findings, with AI `failed` (timeout); job state stays completed |
| `reports/report_short_history.json` | 5 days of history: 4 baseline days, medium confidence, trend buckets 2–4 `insufficient_baseline` and 5–13 `insufficient_data`; latency `unsupported` (no histogram); AI `disabled` |
| `reports/report_partial_source_error.json` | Partial report: network queries timed out (exclusion listed); AI `not_configured` |
| `jobs/job_running.json`, `job_completed.json`, `job_interrupted.json`, `submitted_duplicate.json` | Lifecycle states, including restart interruption and a duplicate submission |
| `api/config.json`, `projects.json`, `envs.json`, `problem_queue_full.json`, `problem_report_not_ready.json` | API bodies |
| `metrics/capabilities_supplied_unverified.json` | Capability manifest from supplied examples only (all `verified=false`); fallback for scopes not yet discovered |
| `metrics/capabilities_paas_production_observed.json` | paas/production capabilities verified live by T003, including `partial` containers and `unsupported` memory limits/throttling |

Fixture values are illustrative. They are not detector output: T005 must derive its own results from series and must not copy them.
