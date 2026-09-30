# T008 — Web interface and evidence exploration

Dependencies: T002, T007. Status: see [task index](README.md).

## Outcome

A user can investigate the latest day and the two-week trend through an accessible, integrated web UI.

## Ownership

`frontend/` and UI tests.

## Work

- Provide project/env selectors, run action, progress, failure/retry states, and a shareable local report route. Changing project resets env and invalidates stale selections/results.
- Present latest-24h findings ordered by severity, affected resources, data coverage, and AI explanation status.
- Show 14-day trend charts with counts, anomalous duration, affected entities, and coverage; allow selection of a day/entity/category.
- Provide finding details with metric chart, expected range, timestamps/timezone, labels, detector explanation, supporting query, and investigation guidance.
- Distinguish no anomalies from no data and unsupported metrics. Clearly label incomplete baselines, mean versus percentile latency, and AI hypotheses.
- Use accessible chart alternatives and keyboard-operable controls. Verify core interactions and narrow-screen layouts in a browser during implementation.

## Acceptance

- [ ] Selecting a project resets dependent env/results correctly; stale responses cannot replace the current selection.
- [ ] The UI supports submission, progress, cancellation, retry, and reopening a saved report.
- [ ] Findings and trends expose severity, confidence, observed values, evidence, and coverage.
- [ ] Charts distinguish gaps, mean/percentile latency, missing baselines, and observed/expected values.
- [ ] AI hypotheses and explanation failures are clearly separated from numerical findings.
- [ ] Desktop, narrow-screen, and keyboard paths work with loading/empty/error/partial states.

## Verification

Run browser scenarios for `paas` / `production` with synthetic report data, then the integrated API. Inspect an episode and trend day; change project during a request; exercise no data, unsupported latency, short history, backend failure, and AI failure. Verify narrow-screen layout, keyboard controls, and chart alternatives.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
