# T001 — Technical decisions and UI blueprint

Dependencies: none. Status: see [task index](README.md).

## Outcome

Subsequent agents have concrete defaults for the application, analysis policy, API boundaries, and main screens.

## Ownership

`docs/DECISIONS.md`, `docs/UI_SPEC.md`, and justified architecture updates.

## Work

- Read the product plan, architecture, and supplied metric examples. Preserve the distinction between confirmed requirements and proposed implementation defaults.
- Resolve current compatible Python/FastAPI, React/TypeScript, SQLite tooling, OpenAI SDK, package managers, and charting choices from official documentation; record exact versions and lookup dates under the architecture's version policy.
- Define API/error conventions, schema tooling, job stages, queue limits, duplicate submissions, cancellation, and restart behavior.
- Specify configurable query budgets, report/evidence storage limits, detector defaults, baseline coverage criteria, and numerical severity/confidence policies. Keep threshold choices explicit and provisional, not SLOs.
- Design Overview, Findings, Trends, and finding evidence views, including project/env dependency, saved-report navigation, desktop/narrow-screen behavior, loading/empty/error/partial states, and keyboard use.
- Record decisions in `docs/DECISIONS.md` and `docs/UI_SPEC.md`. Resolve ordinary choices without another approval round; ask only about a material missing requirement.
- Do not scaffold the application or perform deployment in this task.

## Acceptance

- [ ] Required tooling, version/compatibility choices, and their rationale are recorded with current official references.
- [ ] API/job lifecycle, persistence, numerical policy, and resource-budget defaults are concrete enough for subsequent tasks.
- [ ] The UI specification covers all five product workflows and incomplete telemetry/AI states.
- [ ] New implementation defaults are not described as owner-confirmed decisions.
- [ ] Product, architecture, and task dependencies remain consistent.

## Verification

Walk through the five product workflows using synthetic scenarios: healthy data, sustained CPU load, a 404 increase, missing histogram buckets, short retention, AI failure, and reopening a saved report. Review documentation consistency; application scaffolding is not required.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
