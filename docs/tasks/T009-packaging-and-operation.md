# T009 — Local packaging and operation documentation

Dependencies: T007, T008. Status: see [task index](README.md).

## Outcome

A fresh checkout can run the packaged app with persistent reports and documented configuration.

## Ownership

Docker/build configuration, `.env.example`, root README, and `docs/OPERATIONS.md`.

## Work

- Package the backend and built UI for local execution using the runtime/dependency choices recorded in T001/T002.
- Persist SQLite reports in a volume and document non-destructive startup, shutdown, migrations, and restart behavior.
- Document native and Docker setup, required/optional settings, supported metrics, thresholds, history requirements, and troubleshooting.
- Preserve `METRICS_URL=http://localhost:8428` for native execution; explain container localhost and provide an appropriate configurable host-address/network option.
- Keep keys and metrics credentials server-side. Local exposure is the initial scope; do not publish a service or modify the metrics backend.
- Document source/query/runtime budgets, report storage limits, and a non-destructive method to preserve/copy saved reports.
- Replace the default GitLab README with accurate setup/use links once the application is available.

## Acceptance

- [ ] Documented native and Docker startup work from a fresh checkout with reproducible dependencies/images.
- [ ] Packaged UI/backend operate together with the configured metrics URL and optional AI settings.
- [ ] Saved reports survive container recreation without deleting the volume.
- [ ] Connectivity and invalid-configuration failures are understandable and documented.
- [ ] Runtime, persistence, migrations, storage limits, and local exposure match the documentation.

## Verification

Start the packaged app in an isolated local setup, produce a synthetic report, recreate the application container while preserving its volume, and reopen the report. Check native and container URL configuration. Record exact commands and environment limitations; external deployment is not required.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
