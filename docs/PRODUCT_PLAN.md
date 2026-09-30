# DevOps AI assistant — product and design plan

Status: planning only, 2026-09-30. Only confirmed requirements below are owner decisions. Technology and interface details are proposed defaults. No implementation has started.

Implementation handoff: [ordered task backlog](tasks/README.md). Technical boundaries and shared behavior: [architecture](ARCHITECTURE.md). The backlog defines the first-release scope; future ideas are not additional release requirements.

## Confirmed requirements

- Web UI first, built with Vue.js (owner decision, 2026-09-30); CLI and scheduled daily reports later.
- Read metrics through a Prometheus-compatible HTTP API configured by environment: `METRICS_URL=http://localhost:8428`.
- Select exactly one `project` and `env` per analysis, with discovery-backed selectors.
- Analyze anomalies in the latest 24 hours and show both historical comparison and a two-week anomaly trend.
- Cover node exporter, cAdvisor, and OpenTelemetry HTTP/RPC metrics where available.
- Start with OpenAI; keep the explanation provider replaceable.
- No existing SLOs. Initial thresholds are configurable diagnostic heuristics, never represented as SLO violations.

## Starting material

The repository contains a default GitLab README and planning documents, with no application code. The owner provided a metrics endpoint and representative series; live connectivity, retention, cardinality, and exporter capabilities have not been checked.

| Input | Supplied example |
| --- | --- |
| Metrics endpoint | `METRICS_URL=http://localhost:8428` |
| Selection labels | `project="paas", env="production"` |
| Node metric | `node_cpu_seconds_total`, including idle, iowait, and irq modes |
| Node identity | `instance="paas-production", job="node"` |
| HTTP counter | `http_server_request_duration_seconds_count` |
| HTTP identity | `instance="10.0.4.251:5555", job="dispatcher-api"` |
| HTTP dimensions | `http_request_method`, `http_response_status_code`, `http_route`, `error_type` |
| HTTP examples | GET requests with 200 and 404 statuses, including `/api/v3/tasks/:task` |

These examples establish names and labels, not historical behavior. Container/RPC metrics, histogram buckets, resource limits, restart signals, and service-to-host mappings still need discovery.

## First usable release

| Area | Scope |
| --- | --- |
| Selection | Discover projects and dependent environments; run one exact project/env scope at a time. |
| Latest 24 hours | Rank anomaly episodes by severity, resource, duration, confidence, and evidence. |
| Historical comparison | Compare the latest day with preceding observations, disclosing baseline length and missing data. |
| Two-week trends | Show daily episode counts, anomalous duration, severity, affected resources, and observation coverage. |
| Infrastructure | CPU, memory, filesystem/inodes, disk I/O, network, and supported container resource/event signals. |
| Requests | HTTP/RPC traffic, failure rates, and latency where source metrics support it; distinguish HTTP 4xx from 5xx. |
| Explanation | OpenAI-generated summaries, evidence-linked hypotheses, and investigation steps through a replaceable provider. |
| Reports | Persist analysis results and evidence so a report can be reopened after process restart. |
| Delivery | Documented local/native and Docker execution, with explicit limitations. |

No SLOs were supplied. Severity uses documented diagnostic heuristics and must not be called an SLO breach. Missing telemetry cannot be represented as a healthy resource.

## Proposed navigation and visual direction

Primary report views: **Overview · Findings · Trends**. Project/env selection and the analyzed time window remain visible. A finding opens its evidence and metric chart without losing the selected report.

- Lead with the last-24-hour findings and data coverage; put AI commentary alongside the evidence it discusses.
- Provide dependent project/env selectors, an Analyze action, job progress, retry/cancel behavior, and a route for reopening a saved report.
- Findings show affected entity, severity, confidence, time span, observed versus expected behavior, and investigation guidance.
- Trends show 14 consecutive daily buckets and can be filtered by entity or category. Missing history is visibly incomplete.
- Detail charts include units, timestamps, expected range where available, gaps, and the supporting query.
- Distinguish healthy observations, unsupported signals, insufficient data, and source failures in both text and charts.
- Support keyboard navigation and chart alternatives. On narrow screens use a full-width detail view rather than a cramped side panel.
- UI styling, charting tools, exact layouts, and initial interface language are implementation defaults resolved in T001.

## Core workflows

1. **Investigate today:** choose project/env → run analysis → inspect ranked findings → open evidence → read hypotheses and investigation steps.
2. **Assess deterioration:** open Trends → compare the daily measures and coverage → select a recurring resource/problem → inspect supporting episodes.
3. **Work with incomplete telemetry:** run analysis with missing histograms or short history → see supported findings and precise omissions → understand reduced confidence.
4. **Use results without AI:** OpenAI is disabled or unavailable → open the numerical report and evidence → see explanation status separately.
5. **Revisit a result:** reopen a saved report after a process restart → see its original scope, end time, detector version, findings, and evidence.

## Delivery sequence

1. Decisions, UI blueprint, shared contracts, and read-only telemetry inventory.
2. Metrics queries, deterministic analysis, and provider-independent explanations.
3. Persistent analysis jobs/API and an integrated web UI.
4. Local packaging, documented operation, and verified first-release workflows.

The [task index](tasks/README.md) supplies exact dependencies and agent assignments.

## First-release acceptance criteria

- A user can select a discovered project/env pair, run analysis, and reopen its saved report.
- Every metric query and returned report remains inside the selected scope.
- Findings identify affected resources, timestamps, severity, confidence, observed/expected behavior, and inspectable evidence.
- The last-24-hour report and 14-day trend use consistent, versioned calculations without training on future observations.
- Coverage and baseline limitations remain visible; unsupported latency or restart metrics do not produce invented results.
- HTTP 404 increases and HTTP 5xx failures are distinguished; entity relationships are supported by actual labels/mappings.
- AI output references real findings, labels hypotheses honestly, and cannot prevent numerical reporting when it fails.
- Report persistence, cancellation, restart handling, desktop/narrow-screen interaction, and keyboard access have recorded verification.
- A fresh checkout can run locally from documented configuration without modifying the source metrics backend.

## Deferred work and unresolved inputs

CLI entry points, scheduled daily reports/delivery channels, additional AI adapters, user SLOs, suppression/maintenance windows, longer seasonal baselines, and multi-user authentication follow this release.

Retention, scrape cadence, actual cAdvisor/RPC metric names, histograms, and service-to-host mapping are discovery work. The hosting target and OpenAI model remain configurable. Python/FastAPI, TypeScript, SQLite, and Docker are proposed defaults, not a copied family-tree stack or confirmed owner choices; the Vue.js frontend is confirmed.
