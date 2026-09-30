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

- [x] Required tooling, version/compatibility choices, and their rationale are recorded with current official references.
- [x] API/job lifecycle, persistence, numerical policy, and resource-budget defaults are concrete enough for subsequent tasks.
- [x] The UI specification covers all five product workflows and incomplete telemetry/AI states.
- [x] New implementation defaults are not described as owner-confirmed decisions.
- [x] Product, architecture, and task dependencies remain consistent.

## Verification

Walk through the five product workflows using synthetic scenarios: healthy data, sustained CPU load, a 404 increase, missing histogram buckets, short retention, AI failure, and reopening a saved report. Review documentation consistency; application scaffolding is not required.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts: `docs/DECISIONS.md` (new), `docs/UI_SPEC.md` (new), `docs/ARCHITECTURE.md` (links to decisions), `docs/tasks/README.md` (status, next assignment), `docs/tasks/T001-decisions-and-design.md`, `PLAN.md`.
- Commands/checks and results: version lookups against the PyPI JSON API, npm registry, Node.js release index, Python release-cycle data, and Docker Hub tags (all 2026-09-30); verified the `typescript-eslint` peer range (`typescript <6.1.0`) and numpy cp314 wheels; checked the OpenAI Python SDK docs for `responses.parse(text_format=...)`. Local tools observed: uv 0.12.19, Node 26.10.0 (build target is Node 24 LTS), Python 3.14 available, Docker 29.6.1, sqlite3 3.54.0. Walked through the seven synthetic scenarios in `UI_SPEC.md` §11 against the product workflows. No application code or scaffolding was created.
- Decisions or dependency changes: TypeScript pinned to 6.0.x (compatibility exception); Node 24 LTS instead of Node 26 Current; SQLAlchemy Core + Alembic for migrations; ECharts for charts; no automatic report pruning. Task dependencies unchanged.
- Remaining limitations or blockers: all thresholds are provisional heuristics pending T003 data (scrape interval, retention, container/RPC names, histograms). The OpenAI model is not selected (T006 verifies it). The Docker Node build-stage tag and bundled SQLite version are verified in T009. The live metrics source was not contacted (T003).
- Next ready task: T002 (T003 also ready).
