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

- [x] T001–T009 are DONE with reviewable evidence and no unresolved implementation blocker.
- [x] All five product workflows pass through the packaged application.
- [x] Scope isolation, numerical evidence, baseline windows, and trend comparability hold end to end.
- [x] Missing capabilities and AI/source failures produce honest, usable results.
- [x] Persistence and interrupted-job behavior are verified across restart.
- [x] Live versus synthetic verification and any external access limitations are explicit.
- [x] README and release checklist match the delivered app and its actual verification.

## Verification

Record these flows against the packaged application:
1. Fresh startup → select project/env → run analysis → open finding evidence → reopen saved report.
2. Inspect 14-day trend → filter entity/category → select daily episodes and coverage.
3. Run with short history, missing buckets, and unavailable container/RPC signals → inspect precise omissions.
4. Disable/fail AI → retrieve the complete numerical report and explanation status.
5. Cancel a run and interrupt another → restart → verify statuses, saved reports, and evidence.

Use synthetic data for committed tests and shareable screenshots. Add the live bounded smoke result when available, and do not describe unavailable external checks as passed.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `docs/RELEASE_CHECKLIST.md` (new)
  - integration fixes: `backend/app/jobs.py` (cancelled jobs have no error) with a regression test in `backend/tests/test_api.py`; `frontend/src/api/queries.ts` (background polling)
  - `README.md`, `PLAN.md`, `docs/tasks/README.md`
- Commands/checks and results:
  - `make check` passes (269 backend tests passed, 2 skipped; 16 frontend tests; build).
  - `docker build` passes.
  - All five product workflows passed through the packaged image, flows 1, 2 and 5 against the live tunnelled VictoriaMetrics. Details are in `docs/RELEASE_CHECKLIST.md`.
  - Live large-scope run on paas-gpu/production took 200 s, with 292 findings, a 12.8 MB report and no truncation.
- Decisions or dependency changes: none beyond the fixes listed above.
- Remaining limitations or blockers:
  - OpenAI was not verified live (no key).
  - Thresholds are noisy on bursty fleets and need owner tuning.
  - Large reports are slow to load in the browser.
  - No auth, CI file, or published image.
  - Linux `host-gateway` was not exercised.
  - See the checklist for the full list.
- Next ready task: none. The first-release backlog is complete. Suggested follow-ups: live OpenAI check, threshold tuning with the owner, CI pipeline, access control before any non-local exposure, then the deferred CLI and scheduled reports.
