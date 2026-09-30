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

- [x] Selecting a project resets dependent env/results correctly; stale responses cannot replace the current selection.
- [x] The UI supports submission, progress, cancellation, retry, and reopening a saved report.
- [x] Findings and trends expose severity, confidence, observed values, evidence, and coverage.
- [x] Charts distinguish gaps, mean/percentile latency, missing baselines, and observed/expected values.
- [x] AI hypotheses and explanation failures are clearly separated from numerical findings.
- [x] Desktop, narrow-screen, and keyboard paths work with loading/empty/error/partial states.

## Verification

Run browser scenarios for `paas` / `production` with synthetic report data, then the integrated API. Inspect an episode and trend day; change project during a request; exercise no data, unsupported latency, short history, backend failure, and AI failure. Verify narrow-screen layout, keyboard controls, and chart alternatives.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `frontend/src/api/{client,queries}.ts`, `frontend/src/lib/{format,charts}.ts`
  - components: SeverityChip, StatusBadge, ConfidenceBadge, FindingCard, ExplanationPanel, CoverageTable, ChartBox with lazy ECharts, DataTable
  - views: `StartView`, `JobView`, `report/{ReportLayout,OverviewView,FindingsView,EvidencePanel,TrendsView,context}`
  - `router.ts` (UI_SPEC §1 routes), `styles/tokens.css`, `App.test.ts`
  - `docs/screenshots/*.png` (synthetic data); `.gitignore` (`.playwright-mcp/`)
- Commands/checks and results:
  - `make check` passes (frontend: vue-tsc, ESLint with 0 warnings, API-type drift check, 16 Vitest tests, build).
  - The Vitest suite drives the router and query stack with fixture-backed fetch mocks. It covers:
    - project change resets env; a stale env response for a previous project is ignored; single env selected
    - Analyze disabled until an env is selected; queue full shown with its Retry-After; unreachable source
    - job stages and inline-confirmed cancel; restart-interrupted message; redirect to the report
    - overview hypotheses labelled unverified and separate from findings; healthy report shown only with coverage
    - AI failure; partial banner and omissions
    - short history: latency Unsupported with its histogram reason; 3 insufficient-baseline and 9 insufficient-data days; inconclusive trend
    - URL filters; evidence with chart label, query and "Show data" gaps; Esc keeps filters
    - j/k navigation, ignored in inputs; trends day drill-down and the recurring table
    - unavailable-report message; arrow-key tabs with focus management
  - Browser run (Playwright, Chromium), with the backend on `METRICS_URL=synthetic://incident` + `AI_PROVIDER=fake` and the Vite dev server:
    - Start → Analyze → progress → report (3 findings).
    - Overview, Findings, Evidence (chart with episode band, expected range, heuristic line) and Trends at 1366 px; overlay detail at 1024 px; full-width detail at 390 px.
    - `k` and `Esc` work, and focus returns to the row.
    - Horizontal overflow is 0 on all report views at 390 px.
    - The console showed no errors after two fixes found during this run: prop fallthrough on the layout, and ECharts markArea timestamps not matching.
  - Main bundle 183 KB (uncompressed); ECharts is a separate 617 KB chunk, lazy-loaded when a chart first renders.
- Decisions or dependency changes:
  - Native `<select>` elements are used for the combobox (keyboard type-ahead, accessible).
  - A single discovered env is auto-selected.
  - Filters and the selected day are URL state; the time zone is a per-viewer localStorage preference with UTC as default.
  - No new dependencies.
- Remaining limitations or blockers:
  - Browser checks were run interactively through Playwright MCP, not as a committed e2e suite.
  - Screen-reader output was not tested with real assistive technology; charts rely on aria descriptions plus data tables.
  - The integrated UI against the live source was exercised through the API in T007, not through the browser.
- Next ready task: T009.
