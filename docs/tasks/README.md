# Implementation tasks and agent handoff

This is the execution index for the first usable DevOps AI assistant release. Read [the product plan](../PRODUCT_PLAN.md) and [architecture](../ARCHITECTURE.md) before starting. Web UI, project/env selection, 24-hour analysis, two-week trends, and OpenAI-first explanations are confirmed. The Vue.js frontend is an owner decision (2026-09-30); Python/FastAPI, TypeScript, SQLite, and Docker are proposed defaults made concrete in T001.

All tasks T001–T010 are DONE; the projects feature (T011–T013) is DONE. Multiple data sources with Cloudflare as the second kind (T014–T016) are DONE; Sentry is the third kind (T017, DONE), Wazuh the fourth (T018, DONE) and Grafana alerts the fifth (T019, DONE). Custom PromQL rules per project (T020), credentials (T021) and scope diagnostics (T022) are DONE. The first release is validated; see [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md) for evidence and remaining limitations. This index is the single source of task status; acceptance checkboxes and completion evidence live in each task file. Creating these documents does not start or complete any task.

T023 records the proposed Related metrics feature for future incident investigation work. Its numerical defaults remain subject to implementation-time validation.

## Ordered backlog

| ID | Task | Dependencies | Status |
| --- | --- | --- | --- |
| T001 | [Technical decisions and UI blueprint](T001-decisions-and-design.md) | — | DONE |
| T002 | [Application workspace and shared contracts](T002-workspace-and-contracts.md) | T001 | DONE |
| T003 | [Read-only telemetry discovery](T003-telemetry-discovery.md) | T001 | DONE |
| T004 | [Metrics client and scoped query catalog](T004-metrics-client-and-queries.md) | T002, T003 | DONE |
| T005 | [Anomaly detection and two-week trends](T005-anomaly-detection-and-trends.md) | T002 | DONE |
| T006 | [OpenAI explanations and provider abstraction](T006-ai-explanations.md) | T002, T005 | DONE |
| T007 | [Analysis API, jobs, and report persistence](T007-analysis-api-and-persistence.md) | T004, T005, T006 | DONE |
| T008 | [Web interface and evidence exploration](T008-web-interface.md) | T002, T007 | DONE |
| T009 | [Local packaging and operation documentation](T009-packaging-and-operation.md) | T007, T008 | DONE |
| T010 | [First-release acceptance and agent handoff](T010-release-validation.md) | T009 | DONE |
| T011 | [Project model, encrypted source config, and project API](T011-projects-model-and-api.md) | T010 | DONE |
| T012 | [Project-scoped analyses, reports, and legacy migration](T012-project-scoped-analysis.md) | T011 | DONE |
| T013 | [Projects UI](T013-projects-ui.md) | T012 | DONE |
| T014 | [Multi-source foundation](T014-multi-source-foundation.md) | T013 | DONE |
| T015 | [Cloudflare source: edge traffic and security events](T015-cloudflare-source.md) | T014 | DONE |
| T016 | [Sources UI (Cloudflare)](T016-sources-ui.md) | T015 | DONE |
| T017 | [Sentry source: application errors and transactions](T017-sentry-source.md) | T016 | DONE |
| T018 | [Wazuh source: host security alerts](T018-wazuh-source.md) | T017 | DONE |
| T019 | [Grafana alerts source: alert rule state history](T019-grafana-alerts-source.md) | T018 | DONE |
| T020 | [Custom rules: user PromQL analysed with the catalog](T020-custom-rules.md) | T019 | DONE |
| T021 | [Credentials and per-kind source authentication](T021-shared-credentials.md) | T020 | DONE |
| T022 | [Scope diagnostics: what each source's filter selected](T022-scope-diagnostics.md) | T021 | DONE |
| T023 | [Related metrics for incident investigation](T023-related-metrics.md) | T022 | TODO |

## How the next agent should work

1. Read this index, the product plan, architecture, selected task, and completion notes for dependencies. Inspect the repository and applicable `AGENTS.md` instructions before editing.
2. Select the lowest-numbered TODO task whose dependencies are DONE. Change its status here to IN_PROGRESS. If a task is already IN_PROGRESS, inspect actual progress and resume it.
3. Implement only the assigned task and necessary dependency fixes. Preserve unrelated user changes. Future-task stubs do not count as completed features.
4. Resolve ordinary choices using documented defaults and current official documentation. T001 makes choices concrete without another approval round. Ask only when a missing requirement materially prevents correct work.
5. Validate acceptance criteria using the task's verification guidance. Use synthetic/sanitized telemetry in committed fixtures and screenshots.
6. Update acceptance checkboxes and the completion record with actual changed files, commands/results, remaining limitations, and decision/dependency changes.
7. Mark DONE here only when the acceptance criteria are met. If blocked, mark BLOCKED and record the specific blocker and what resolves it. Never silently skip a requirement.
8. End with a short handoff identifying the completed task and next ready task. Continue further only if the assignment authorizes multiple tasks.

Statuses: TODO, IN_PROGRESS, DONE, BLOCKED. Update the index and affected task together. If scope/order changes, update dependencies and relevant product/architecture text.

## Milestones

- **T001–T003:** technical/UI decisions, runnable foundations, shared contracts, and an honest telemetry inventory.
- **T004–T006:** scoped collection, numerical anomalies/trends, and replaceable AI explanations.
- **T007–T008:** persistent analysis jobs and a usable integrated web interface.
- **T009–T010:** reproducible local packaging and verified first release.

## Parallel work and ownership

The numbered order is the default for a single agent. If an assignment explicitly authorizes parallel agents, T002 and T003 can proceed after T001; T004 and T005 can proceed once their listed dependencies are DONE. T006 follows T005. T007 integrates collection, detection, and explanations; T008 integrates the UI with that API.

Shared fixtures allow UI design/prototyping while backend work proceeds, but T008 cannot be marked complete before its dependencies. Follow task ownership to avoid overlapping edits. The T002 owner coordinates manifest/lockfile and shared-schema changes during parallel work; update dependent tasks when a contract changes. Do not spawn agents merely because a task appears here.

## Shared completion rules

- Respect confirmed requirements and distinguish them from implementation defaults.
- Scope every metrics selector to its exact project/env where selected, including ratio operands. Do not mix caches or reports across scopes.
- Preserve metric semantics: rates before aggregation, valid histogram support, separate 4xx/5xx, explicit missing signals, and no invented host/service relationships.
- Calculate baselines only from preceding observations; use consistent resolution and detector versions across trend days.
- Keep numerical results independent of AI. AI failures cannot suppress usable findings; hypotheses require valid evidence references.
- Keep credentials server-side and out of fixtures, logs, and report evidence. Live telemetry checks are bounded and read-only.
- Verify real persistence and restart behavior where essential; mocked storage alone is insufficient.
- UI work covers loading, empty, error, partial, keyboard, and narrow-screen states.
- Record what actually ran; never report unavailable live sources or unrun checks as passing.
- Keep run instructions/configuration current. Public hosting, infrastructure remediation, CLI, and scheduled delivery are outside this release.
- T003 may document an inaccessible source using explicit unverified capabilities. T010 must disclose remaining live-verification gaps even when all fixture-based acceptance passes.

## Suggested assignment for the next agent

> When implementation is assigned, start with T023: add Related metrics to finding details using deterministic comparisons, explicit resource associations and persisted chart evidence. Read its proposal and settle the numerical policy and contracts before implementing the UI. The current assignment only records the proposal.

Other suggested follow-ups from the first release remain: live OpenAI verification (`scripts.check_openai --structured`), threshold tuning with the owner (paas-gpu noise), a CI pipeline running `make check` and `docker build`, access control before any non-local exposure, and the deferred CLI (reuse `app.service.AnalysisPipeline`). Read `docs/RELEASE_CHECKLIST.md` for remaining validation issues; scheduled analyses are now implemented by `ReportScheduler`.
