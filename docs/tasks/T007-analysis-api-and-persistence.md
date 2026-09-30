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

- [ ] Project/env discovery, analysis submission, progress, cancellation, and report retrieval follow the frozen contract.
- [ ] Collection, numerical analysis, and optional AI use one immutable scope/end time/configuration version.
- [ ] Job transitions, duplicate submissions, queue limits, cancellation, and restart recovery behave as documented.
- [ ] Completed/partial report data and evidence survive restart in real SQLite storage.
- [ ] Source failure, insufficient data, unsupported metrics, and AI failure remain distinct.
- [ ] The application service is reusable by future CLI and scheduling adapters.

## Verification

Use the real SQLite repository and deterministic source/provider adapters to exercise submission through retrieval, partial results, cancellation, duplicate requests, full queue, process interruption, and restart. Verify cross-scope isolation and a successful numerical report when AI fails.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
