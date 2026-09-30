# Architecture and implementation boundaries

Status: planning, 2026-09-30. No application is implemented. T001 defaults are recorded in [DECISIONS.md](DECISIONS.md) and [UI_SPEC.md](UI_SPEC.md). The [product plan](PRODUCT_PLAN.md) records confirmed requirements; [T001](tasks/T001-decisions-and-design.md) makes the remaining defaults concrete.

## Proposed stack and runtime

Use a Python backend with FastAPI and a React/TypeScript web UI, packaged for local/self-hosted Docker operation. These are planning defaults, not requirements supplied by the user. Keep the analysis engine independent of HTTP, UI, scheduling, and AI providers so future entry points reuse it.

Use SQLite for analysis job status and report snapshots. Start with one application process and a bounded background job runner; Redis, distributed workers, and a separate time-series database are unnecessary for the first version. Metrics remain in the existing metrics backend.

The backend reads `METRICS_URL`, optional metrics authentication settings, `AI_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. Select an available model during implementation; do not hard-code a model into domain logic. The browser calls only the application backend. Local execution is the initial deployment assumption; deployment beyond localhost requires a separate access-control decision.

Port 8428 suggests single-node VictoriaMetrics, but the product has not been verified. Support the common Prometheus API and preserve any configured URL path prefix. VictoriaMetrics documents this port and its query URL formats in its [API examples](https://docs.victoriametrics.com/url-examples/).

## Version policy

- Resolve current stable, mutually compatible runtimes, frameworks, SDKs, and tooling when T001/T002 execute. Record versions, lookup date, official sources, and reasons for compatibility exceptions in `docs/DECISIONS.md`.
- Pin selected runtime/image versions and commit dependency lockfiles. Keep native startup, build, CI, and Docker configuration consistent.
- Recheck decisions during setup if they have become stale. Do not copy runtime or package versions from the family-tree project.
- Model selection remains configuration; verify availability and the structured-output capability required by the OpenAI adapter at implementation time.

## Application boundaries

| Boundary | Responsibility | Proposed ownership |
| --- | --- | --- |
| Domain/contracts | Versioned inputs, reports, findings, evidence, interfaces | `backend/app/domain/` |
| Metrics | Capability discovery, scoped query catalog, bounded collection | `backend/app/metrics/` |
| Analysis | Baselines, detectors, episodes, correlation, daily trends | `backend/app/analysis/` |
| AI | Bounded evidence explanation, provider adapters, output validation | `backend/app/ai/` |
| Application/API | Job lifecycle and framework-independent orchestration | `backend/app/api/` and orchestration module |
| Persistence | Job status, report snapshots, schema migrations | Backend storage module |
| Web | Scope selection, job progress, findings, trends, evidence | `frontend/` |
| Packaging | Local/native and container execution, operation documentation | Root configuration and deployment docs |

Logical flow: browser → API/job runner → metrics source → deterministic analysis → optional AI explanation → saved report → browser. Future CLI/scheduled entry points invoke the same analysis service.

## Time windows and baseline policy

- Capture one immutable UTC end time `T` for a run. Latest-day analysis is `[T-24h, T)`; trend is 14 consecutive 24-hour buckets spanning `[T-14d, T)`.
- For each daily bucket, train its baseline only on preceding data, up to 14 days. Never use the analyzed bucket or later observations in its own baseline.
- Prefer 28 days of source history plus query lookback to support all 14 trend buckets with full baselines. The latest-day comparison alone needs 15 days for a full 14-day baseline.
- With shorter retention, use available preceding history and expose its length. Provisional minimum: three adequately covered baseline days. Below that, run supported absolute checks and mark relative detection unavailable.
- Start with five-minute evaluation steps and scrape-aware rate windows. Use the same detector resolution and version across trend days; downsample only chart display. Expose resolution because short spikes can be missed.
- Begin with robust median/MAD baselines, minimum effect sizes, and persistence rules. Handle zero MAD explicitly. Consider time-of-day bands only when enough independent days are available; two weeks does not establish reliable weekly seasonality.
- Separate severity from confidence. Coverage, sample volume, and baseline length affect confidence; magnitude and duration affect severity.
- Trend measures episode count, anomalous minutes, peak severity, and affected entities. Also show anomalous duration divided by observed eligible entity-time so added hosts and data gaps do not masquerade as deterioration.
- Missing/partial data is never healthy by default. Preserve coverage and exclusions in reports and charts.

## Shared contracts

| Contract | Required content |
| --- | --- |
| `AnalysisRequest` | Exact project/env, optional end time, detector configuration version |
| `MetricSeries` | Metric family, resource identity, labels, unit, timestamps/values, query, step, coverage |
| `MetricCapability` | Supported/unsupported/partial, required metrics/labels, reason, available history |
| `Finding` | Stable ID, detector/version, entity, start/end, severity, confidence/reasons, observed and expected values, evidence references |
| `DailyTrend` | Bucket bounds, episodes, anomalous minutes, affected entities, observation denominator, coverage |
| `AnalysisReport` | Scope, windows, capabilities, findings, trends, evidence/chart series, exclusions, configuration version, AI status/output |
| `Explanation` | Summary, hypotheses with finding IDs, investigation steps, uncertainty, provider/model metadata |

Freeze units, nullable fields, error codes, JSON examples, and ownership of evidence IDs. Define `MetricsSource`, `AnalysisService`, `ExplanationProvider`, and report repository interfaces. Use an explicit schema version for saved reports.

## Resource and HTTP semantics

Node identity starts with `(project, env, job, instance)`. Preserve device/mountpoint for disk findings. Container identity needs discovery of actual container labels. Service identity prefers an explicit service label, falling back to `job`; preserve route/method and instance for drill-down.

The supplied node instance `paas-production` and HTTP instance `10.0.4.251:5555` are different namespaces. Never infer a service-to-host relationship from these alone. Cross-layer attribution needs an explicit mapping or shared identifying labels; temporal overlap can only support a hypothesis.

Support the supplied `node_cpu_seconds_total` and `http_server_request_duration_seconds_count` with labels `http_response_status_code`, `http_request_method`, `http_route`, and `error_type`. Calculate HTTP 5xx failures and 4xx anomalies separately. The supplied `error_type="404"` is not grounds to classify a request as a server failure.

Discover histogram buckets/native histograms before enabling p95/p99 latency. Count-only data supports traffic and status ratios, not latency. Sum/count supports mean latency only. OpenTelemetry defines HTTP duration as a histogram with route/method/status attributes in its [HTTP metrics conventions](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/).

## API and job lifecycle

Proposed routes: `GET /api/projects`, `GET /api/projects/{project}/envs`, `POST /api/analyses`, `GET /api/analyses/{id}`, `GET /api/analyses/{id}/report`, and `DELETE /api/analyses/{id}` for cancellation. T001 freezes exact route/error conventions; T002 supplies contract examples.

Persist queued/running/completed/partial/failed/cancelled state and expose collection/detection/explanation progress. T001 specifies duplicate requests, queue limits, cancellation, and restart handling. Recovery must not leave abandoned jobs permanently running. AI failure does not erase numerical results.

The same frozen UTC end time, source identity, scope, detector/configuration version, and evidence IDs apply throughout a job. Cache keys must include scope and relevant query/window/version parameters.

## Persistence and operation

SQLite stores jobs and versioned report snapshots; the existing metrics backend remains the time-series source of truth. Persist sufficient evidence/chart data to reopen a report without relying on unchanged source retention. Bound saved payloads and document retention/cleanup policy without silently discarding evidence.

Use explicit migrations and persistent container storage. Startup must not erase saved reports. Verify restart behavior with real SQLite storage.

`METRICS_URL=http://localhost:8428` is the native default supplied by the owner. Inside Docker, localhost refers to the container; document a suitable host address/network option. Preserve configured URL prefixes and keep credentials server-side. Package for local operation; public exposure and external deployment are separate work.

## Decisions delegated to T001

Resolved 2026-09-30 in [DECISIONS.md](DECISIONS.md) and [UI_SPEC.md](UI_SPEC.md) as implementation defaults. Originally delegated: record package managers/runtime versions, frontend routing/charting/styling, API/error conventions, report schema tooling, SQLite access/migrations, job scheduling/cancellation/recovery, query/resource budgets, default detector configuration, and UI behavior in `docs/DECISIONS.md` and `docs/UI_SPEC.md`.

Ordinary implementation choices can be resolved using these defaults and current official documentation. Do not turn proposed choices into owner-confirmed requirements. Shared contracts are frozen in T002 before dependent tasks proceed.

## Scope boundary

The first-release scope is [T001–T010](tasks/README.md). Numerical detection works without an AI provider. Infrastructure changes, remediation, arbitrary model-generated queries, CLI, scheduled delivery, and additional providers are outside this release. Telemetry discovery and acceptance checks are read-only against the supplied metrics source.
