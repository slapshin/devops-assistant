# T013 — Projects UI

Dependencies: T012. Status: see [task index](README.md).

## Outcome

The main page is the project list. From it the user can see each project's health and latest result at a glance, start an analysis, and manage projects. Reports show which project they belong to.

## Ownership

`frontend/src/views/` (new `ProjectsView`, `ProjectForm`, `ProjectView`), `frontend/src/api/queries.ts`, `frontend/src/router.ts`, `docs/UI_SPEC.md`. `StartView.vue` is removed.

## Work

### Routes

| Path | View |
| --- | --- |
| `/` | Project list |
| `/projects/new` | Create form |
| `/projects/:projectId` | Project detail: config summary plus analysis history (paged `GET /analyses?project_id=`) |
| `/projects/:projectId/edit` | Edit form, with delete |
| `/analyses/:id`, `/reports/:id/...` | As today. The header gains a project breadcrumb linking to `/projects/:projectId` |

### Project list (`/`)

Each row or card shows:

- name, matchers as chips, and the source host (`url_display`)
- **source health** badge, fetched lazily per project from `/projects/{id}/health`: reachable, auth failed, unreachable, no matching series, not configured, or credentials unreadable
- **latest report summary**: state, relative time with an absolute tooltip, and severity counts. Links to the report, or to the job while it is active
- **14-day anomaly sparkline** from `trend` (ECharts, reusing the `EvidenceSparkline` styling). Shows a "no report yet" placeholder when there is no data
- **Run analysis** button. While a job is queued or running, the row shows inline stage progress (polling as `JobView` does) and the button is disabled. `queue_full`/`source_not_configured` problems appear as inline messages

Empty state: an explanation plus a "Create project" call to action. Rows are sorted by name, with a client-side text filter once there are more than ~10 projects.

### Project form

- Name, description.
- Matchers: an editable list of `label = value` rows with add/remove and field-level validation that mirrors the backend. Optional autocomplete of values via the label-values helper (T011).
- Prometheus source: URL, TLS verify, and an auth selector (none, bearer, basic). Secret inputs are write-only and show "stored, leave empty to keep" when a secret exists.
- **Test connection** runs against the unsaved draft and shows reachability/auth, matched series count, history days, and a family capability table. Saving does not require a passing test, but a failed or never-run test shows a warning.
- The layout groups the source in a "Sources" section so future Wazuh, Cloudflare, or Sentry sections slot in.
- Delete opens a confirm dialog where the user must type the project name. It states how many reports will be removed and is disabled while a job is active.

### General

- Generated types come from `schema.d.ts`. Server state uses Vue Query, with cache invalidation on create/update/delete/run.
- Loading, empty, error, partial, keyboard, and narrow-screen states are covered, as in T008.

## Acceptance

- [x] Vitest covers: list rendering with all health/latest states, running from the list with inline progress, form validation and secret-keep semantics, the test-connection result display, and delete confirmation.
- [x] Manual flow on `make demo`: create a project → test connection → run → open the report → edit → delete. Desktop and narrow screenshots use synthetic data.
- [x] `docs/UI_SPEC.md` is updated. `make check` passes.

## Completion record

- Completed date: 2026-10-03
- Actual changed files and artifacts:
  - new views: `src/views/projects/ProjectsView.vue` (`/`), `ProjectView.vue`, `ProjectFormView.vue`. `StartView.vue` was removed.
  - new components: `src/components/projects/{ProjectCard,HealthBadge,MatcherChips,TrendSparkline,RunAnalysis,ConnectionTestResult}.vue`, `src/components/{AnalysesTable,SeverityCounts}.vue`
  - `src/lib/projects.ts` (draft model, validation that mirrors the backend, input mapping, health states), `src/lib/format.ts` (`STAGE_LABELS`, `CAPABILITY_LABELS`, `formatAge`), `src/api/{client,queries}.ts` (project queries/mutations, `apiPut`, 204 handling, infinite history), `src/router.ts`, `src/styles/tokens.css`, `JobView.vue` and `ReportLayout.vue` (project links), `src/App.test.ts`
  - backend: `ProjectSummary.report_count`, and `GET /api/projects/{id}` now returns the summary (`ProjectActivity` in `domain/interfaces.py`, `storage/repository.py`, `api/projects.py`); contracts, schema types and fixtures were regenerated
  - docs: `docs/UI_SPEC.md` (§1 routes, §2 frame, §3 projects), `README.md`, screenshots `docs/screenshots/{projects-running-desktop,projects-narrow,project-form-desktop,project-form-narrow,project-detail-desktop,project-delete}.png` (synthetic data)
- Commands/checks and results:
  - `make check` passes (354 backend tests passed, 2 skipped; 29 frontend tests; build). `docker build` passes.
  - Manual flow against the native app with `DEMO_PROJECTS=true AI_PROVIDER=fake` in a temporary data dir, in Playwright at 1280×900 and 390×844: create project → test connection → create → run from list (inline progress) → open report → project link → edit → delete with typed confirmation. The flow found and fixed: card columns shifting while a run is shown, and the deleted project's queries refetching (404 console errors).
- Decisions or dependency changes:
  - The trend sparkline is a small SVG component rather than ECharts, to keep list rows light; it follows the `EvidenceSparkline` approach.
  - Label-value autocomplete (the optional T011 helper) was not built. Matchers are typed, and *Test connection* shows whether they match.
- Remaining limitations or blockers: none. Live checks against a real VictoriaMetrics source and the existing Docker volume upgrade remain for the owner's environment (see T012).
