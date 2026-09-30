# T010 — First-release acceptance and agent handoff

Dependencies: T009. Status: see [task index](README.md).

## Outcome

The first release is assessed against agreed workflows with recorded evidence and accurate installation/use documentation.

## Ownership

`docs/RELEASE_CHECKLIST.md`, integration fixes, and final README updates.

## Work

- Review T001–T009 acceptance and completion evidence; resolve concrete integration defects and do not mark incomplete dependencies DONE.
- Run repository checks appropriate to the delivered behavior and the end-to-end flows below. Reuse meaningful coverage rather than adding tests that merely mirror implementation.
- Verify desktop/narrow-screen and keyboard paths plus loading/empty/error/partial states.
- Run a bounded read-only smoke analysis against the configured metrics source when reachable. Record live capabilities, history, runtime/query measurements, and any differences from fixture verification.
- Keep paid live AI calls out of ordinary automated tests. Clearly distinguish an adapter verified with mocks from a live provider check.
- Write `docs/RELEASE_CHECKLIST.md` with actual commands/results, environment, capability limits, remaining issues, and setup/use links.
- If live metrics remain unreachable, record live compatibility as unverified; do not claim that a synthetic pass validates the owner's backend.

## Acceptance

- [ ] T001–T009 are DONE with reviewable evidence and no unresolved implementation blocker.
- [ ] All five product workflows pass through the packaged application.
- [ ] Scope isolation, numerical evidence, baseline windows, and trend comparability hold end to end.
- [ ] Missing capabilities and AI/source failures produce honest, usable results.
- [ ] Persistence and interrupted-job behavior are verified across restart.
- [ ] Live versus synthetic verification and any external access limitations are explicit.
- [ ] README and release checklist match the delivered app and its actual verification.

## Verification

Record these flows against the packaged application:
1. Fresh startup → select project/env → run analysis → open finding evidence → reopen saved report.
2. Inspect 14-day trend → filter entity/category → select daily episodes and coverage.
3. Run with short history, missing buckets, and unavailable container/RPC signals → inspect precise omissions.
4. Disable/fail AI → retrieve the complete numerical report and explanation status.
5. Cancel a run and interrupt another → restart → verify statuses, saved reports, and evidence.

Use synthetic data for committed tests and shareable screenshots. Add the live bounded smoke result when available, and do not describe unavailable external checks as passed.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
