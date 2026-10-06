# T016 — Sources UI (Cloudflare)

Dependencies: T015. Status: see [task index](README.md).

## Outcome

The project form manages several data sources. Prometheus (URL, TLS, auth, label matchers) and Cloudflare (zone ID, hostnames, API token) can each be added, tested and removed independently. Project and report pages show which sources a project uses and the new edge and security families.

## Ownership

`frontend/src/lib/projects.ts`, `frontend/src/views/projects/*`, `frontend/src/components/projects/*` (new source cards), report views and `lib/format.ts` (family and entity labels), `App.test.ts`, `docs/UI_SPEC.md`.

## Work

- The draft model has one optional block per source kind. Validation mirrors the backend:
  - Cloudflare zone ID is 32 hex characters, with up to 20 hostnames.
  - The token is required unless one is stored.
  - Matchers are required only with Prometheus.
- Form: an "Add source" control, plus one card per source with its own **Test connection** button and result. Label matchers sit inside the Prometheus card. Omitted secrets show as "stored — leave empty to keep".
- Project card and page: source chips (`Prometheus · host`, `Cloudflare · zone/hosts`) and a per-source health summary.
- Report: the sources list in the overview, plus labels and icons for the `edge` and `security` families and the `zone` entity kind.
- Covers the loading, error, empty, keyboard and narrow-screen states.

## Acceptance

- [x] Projects with Prometheus only, Cloudflare only, or both can be created, edited, cloned and tested in the UI.
- [x] Report pages render Cloudflare findings and evidence.
- [x] vitest covers draft ↔ input mapping and validation for both kinds.
- [x] `make check` passes. (Screenshots in `docs/screenshots/` not regenerated; see limitations.)

## Completion record

- Completed date: 2026-10-06
- Actual changed files and artifacts: `frontend/src/lib/projects.ts` (Cloudflare draft fields, per-kind validation and inputs, `sourceKinds`, kind-aware `serverFieldErrors`, `sourceSummary`, `testVolume`, `no_traffic` health), `views/projects/ProjectFormView.vue` (Prometheus section with labels, Cloudflare section, per-source tests), `ProjectView.vue`, `ProjectsView.vue`, `components/projects/{ProjectCard,ConnectionTestResult,HealthBadge,RunAnalysis}.vue`, `views/report/ReportLayout.vue` (source chips), `lib/format.ts` (family labels), `App.test.ts` (Cloudflare create/test, stored token and error mapping, Cloudflare report), `docs/UI_SPEC.md`
- Commands/checks and results: `make check` passes (38 frontend tests). Checked manually in the built app with `DEMO_PROJECTS=true AI_PROVIDER=fake`: project list, edit form with Cloudflare test, and a mixed Prometheus + Cloudflare incident report.
- Remaining limitations: `docs/screenshots/` was not regenerated.
