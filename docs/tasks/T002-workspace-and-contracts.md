# T002 — Application workspace and shared contracts

Dependencies: T001. Status: see [task index](README.md).

## Outcome

The backend and frontend share a reproducible workspace and versioned contracts without depending on live metrics or AI.

## Ownership

Project manifests/lockfiles, settings, `backend/app/domain/`, `docs/contracts.md`, and shared API fixtures.

## Work

- Establish the project layout and dependency/tooling choices.
- Implement and document the [shared contracts and interfaces](../ARCHITECTURE.md#shared-contracts), including a framework-independent analysis entry point.
- Define configuration validation and report schema/version policy.
- Provide shared fixtures: supplied labels, normal behavior, anomalies, missing data, partial history, and unavailable AI.
- Own dependency changes requested by other agents to avoid concurrent lockfile edits.
- Pin and verify the T001 runtime/dependency choices; record startup/build/check commands and configuration validation.
- Freeze exact routes, units, nullable fields, errors, report/evidence IDs, capability states, and schema versions in contract examples before downstream work.
- Produce runnable application foundations and repository checks, while keeping future task stubs visibly incomplete.

## Acceptance

- [ ] Backend/frontend foundations start and their documented build/check commands work.
- [ ] Shared contracts cover every type and interface in the architecture, including partial/error states.
- [ ] Schema examples use the supplied label conventions and permit independent UI/analysis development.
- [ ] Domain logic imports neither LLM SDK types nor web framework request types.
- [ ] Configuration validation and exact runtime/dependency choices are reproducible.

## Verification

Run the foundation build/checks and validate representative JSON examples against the schema. Confirm configuration errors are actionable and no real OpenAI key or metrics connection is required for contract validation.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
