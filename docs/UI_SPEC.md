# UI specification (T001)

Status: recorded 2026-09-30 by T001. Layouts, wording, and styling are **implementation defaults**, not owner-confirmed requirements. The confirmed parts are those in the [product plan](PRODUCT_PLAN.md#confirmed-requirements): a web UI, one project/env per analysis, the latest-day analysis, the two-week trend, and OpenAI-first explanations. Technical choices are in [DECISIONS.md](DECISIONS.md).

Interface language: English (default; strings are kept in one module so they can be translated later).

## 1. Routes

| URL | View |
| --- | --- |
| `/` | **Projects**: one card per project with health, latest report, 14-day trend and Run (T013) |
| `/projects/new`, `/projects/:projectId/edit` | **Project form**: labels, source, Test connection, delete |
| `/projects/:projectId` | **Project**: configuration, Run, analysis history |
| `/analyses/:id` | **Job progress**; switches to the report route when a report exists |
| `/reports/:id` | **Overview** (default report tab) |
| `/reports/:id/findings` | **Findings** list; `?category=&severity=&entity=` filters live in the URL |
| `/reports/:id/findings/:findingId` | Findings list + **Evidence** detail (a full-width page on narrow screens) |
| `/reports/:id/trends` | **Trends**; `?entity=&category=` filters |

Every report URL can be shared and reloaded and works after a process restart, because it is read from the saved snapshot. The browser Back button moves between the list and the detail view without losing filters.

## 2. Persistent report frame

A header appears on every report route:

```
[Assistant]  paas / production   Window: 29 Sep 10:05 – 30 Sep 10:05 UTC (latest 24 h, 5-min steps)
             Status: Completed · Detector detectors-2026.09.1 · Report saved 30 Sep 10:07   [Run again] [New analysis]
Tabs:  Overview | Findings (12) | Trends
```

- The scope and the frozen end time `T` are always visible. Times are shown in UTC by default, with a toggle for local time; tooltips always include UTC.
- The project name links to its project page. *Run again* uses the same project with `T = now`. *Project* returns to the project page.
- A `partial` report shows a persistent amber banner ("Some signals could not be collected — see Coverage"), with a link to the coverage section.

## 3. Projects (T013)

Replaces the original start view (project/env selectors). Screenshots: `docs/screenshots/projects-running-desktop.png`, `projects-narrow.png`, `project-form-desktop.png`, `project-form-narrow.png`, `project-detail-desktop.png`, `project-delete.png`.

**List (`/`).** One card per project, sorted by name:

- Name (links to the project page), label matchers as chips, and the source host (`synthetic: <scenario>` for demo sources).
- **Source health**: fetched lazily per project from `GET /api/projects/{id}/health` (cached 60 s). States: *Reachable*, *Unreachable*, *Auth failed*, *No matching series*, *No source*, *Credentials unreadable*, *Checking…*. Projects that cannot be checked (no source, unreadable credentials) are never probed. Text and icon carry the state; colour is never the only signal.
- **Latest report**: state and relative age (UTC tooltip), linking to the report or, without one, to the job. Shows severity counts, or the error code for a failed run.
- **14-day anomalies**: an SVG bar sparkline of `daily_episodes` (oldest first; the latest day is emphasised; days without enough data are dashed stubs). Its accessible name summarises the total and the latest day. "No trend yet" without a report.
- **Run analysis**: posts `{project_id}`. While a job is queued or running, the card shows "Running · <stage> (done/total)" linked to the job instead of the button, and the list polls every 2 s. `queue_full` shows the Retry-After time inline. Without a source, or with unreadable credentials, the button is disabled with a reason and an Edit link.
- Empty state: an explanation and a *Create project* call to action. Load errors show a Retry button. A text filter (name or `label=value`) appears once there are more than 10 projects.

**Project page (`/projects/:projectId`).** Name, description, matchers, source (host, auth type, TLS note, health), saved report count, Edit and Run. The analysis history pages through `GET /api/analyses?project_id=` (20 per page, *Load older analyses*). The report and job headers link back here.

**Form (`/projects/new`, `/projects/:projectId/edit`).**

- Name, optional description, and **Labels**: rows of `name = value` (add/remove, 1–10). Client validation mirrors the server: label name syntax, reserved `__` prefix, duplicates, required values. Server `422` errors map back to the field.
- **Sources → Prometheus-compatible metrics**: URL (no credentials, query or fragment; `synthetic://<scenario>` allowed; empty means no source), TLS verification, and authentication (none, bearer, basic). Secret inputs are write-only. When a secret of the chosen type is stored, the placeholder reads "Stored — leave empty to keep" and an empty input keeps it. Switching the type requires the new secret. Future source kinds get their own section here.
- **Test connection** runs on the unsaved draft (stored secrets are reused in edit mode) and shows reachability/auth, matching series, history days, and a capability table per signal family. Editing labels or the source after a test marks the result stale. Saving is never blocked by the test, but a missing, stale or failed test (or no source at all) shows a warning next to Save.
- **Delete** (edit only): states how many saved reports will be removed, is disabled while an analysis is queued or running, and requires typing the project name before *Delete permanently* is enabled.

## 4. Job progress view

- The stage list (`discovery → collection → detection → trends → explanation → saving`) shows a status icon, a text label, counts (e.g. "collection 38/52 queries"), and elapsed time. It is an `aria-live="polite"` region that announces stage changes only.
- A **Cancel** button (confirmed inline, not with a browser dialog) calls `DELETE`. The Cancelled state offers *Run again*.
- Failed state: the error `code` and a human message. `interrupted_by_restart` reads "The service restarted while this analysis was running. No report was saved." Both show *Run again*.
- The view polls every 2 s. After 3 consecutive network errors it shows "Lost connection to the assistant — retrying" and keeps retrying with backoff.

## 5. Overview

Order (top to bottom). The page answers "what is wrong, how bad, and what is new" before any detail:

1. **Verdict**: severity counts, a one-line headline of the most severe finding ("CPU on node · paas-production peaked at 95.4 % over 3 h"), its ratio to the usual value, how many findings are new today versus seen before, a link to the most severe finding, and the 14-day trend line. Beside it, a **Blind spots** card names the families that were not evaluated (unsupported, insufficient data, source error) and says that problems there would not show up.
2. **When it happened**: one lane per finding (at most 8) on the latest-day axis. Findings that overlap in time are listed as "coincide in time (not established as related)".
3. **Top findings**: the 5 most severe, as cards: severity, New today / Recurring tag, title, entity, the observed peak with the usual range (or the heuristic), an evidence sparkline, time, duration, state, and confidence. A "View all findings" link.
4. **Check first** (AI explanation panel) beside a **Coverage** tile grid (one tile per family, coloured by status, with text and icon):
   - `succeeded`: the summary. Hypotheses are labelled **"Hypothesis — unverified"** with their likelihood, and each links to its referenced finding chips. Investigation steps follow, then the uncertainty note and "Generated by <provider>/<model>".
   - `disabled` / `not_configured`: "AI explanations are off. The numerical report is complete." (For `not_configured`, a hint names the missing variable.)
   - `failed`: "The explanation could not be generated (<reason>). Findings and evidence below are unaffected."
   - `skipped_no_findings`: no panel. The summary strip already says so.
5. **Coverage and limitations**: a table per signal family (CPU, Memory, Filesystem, Disk I/O, Network, Containers, HTTP/RPC traffic, Failures, Client errors, Latency). Each row has a status, a reason, and the baseline days used. The report's `exclusions` (budget truncation, dropped evidence) are listed here.
6. **Healthy report**: "No anomalies detected in evaluated signals" is shown **only together with** the coverage table, never on its own. If nothing could be evaluated, it says "No signals could be evaluated" instead of claiming health.

### Status vocabulary (text + icon + colour; colour never used alone)

| Status | Meaning | Icon |
| --- | --- | --- |
| Anomalous | ≥ 1 finding | filled triangle |
| No anomaly | Evaluated with adequate coverage, no finding | check |
| Insufficient data | Coverage or baseline below minimum; relative detection unavailable | half-circle |
| Unsupported | Source metrics/labels absent (e.g. no histogram → no latency) | slashed circle |
| Source error | Query failed, timed out, or was truncated | exclamation |
| Not evaluated | Out of scope for this detector version | dash |

Severity chips: Critical / High / Medium / Low, each with a text label. Confidence is shown as a separate text badge ("Confidence: medium") so it is never merged with severity. Thresholds are described as "diagnostic heuristic", never "SLO".

## 6. Findings

- List (desktop: table; narrow: cards). Columns: severity, entity (e.g. `node · paas-production` or `dispatcher-api · GET /api/v3/tasks/:task`), category, time span with duration, observed vs expected (e.g. "5xx 7.8 % vs expected 0.3 % (±0.4)"), and confidence.
- Default sort is severity, then peak time. Sort and filters are controls with labels, and filter state lives in the URL. A **Recurrence** toggle (All / New today / Happened before, `?recurrence=new|seen`) sits above the other filters.
- 4xx findings are titled "Client error increase (4xx)" or "404 increase". 5xx findings are titled "Server error rate (5xx)". The two never share a label.
- An empty list after filtering shows "No findings match these filters" and a Clear filters button.

## 7. Evidence detail

On desktop, a right-hand panel at 45 % width. At widths below 900 px it is a full-width page with a "← Findings" back link. Contents:

1. Title, severity, confidence with its **reasons** (e.g. "4 baseline days (< 7)", "coverage 82 %"), and detector name/version.
2. **Chart** (ECharts, SVG renderer):
   - The observed series and the expected band (median ± scaled MAD, or the absolute threshold line labelled as a heuristic).
   - The shaded episode span. Gaps are drawn as breaks, and a "gap" marker appears in the tooltip.
   - A y-axis with a unit (%, req/s, bytes/s, s) and a UTC time axis.
   - Keyboard-accessible dataZoom, plus a generated `aria` description.
3. A **"Show data" toggle** that renders the same points as a table (time, observed, expected, lower, upper, gap flag). This is the chart alternative.
4. **Query**: the exact PromQL/MetricsQL, step, and range in a copyable code block. Credentials are never shown.
5. **Entity labels**: the relevant labels only (job, instance, route, method, status, device, mountpoint).
6. **Related findings**: only those sharing identity labels. Time-overlap links are labelled "Coincides in time (not established as related)".
7. **AI hypotheses referencing this finding**, if any, each labelled unverified.

Unsupported or insufficient signals have no chart. They show a text block explaining what metric or label is missing, or how much history exists.

## 8. Trends

- **Daily measures** over 14 consecutive buckets (labelled by bucket end date, UTC), with a series selector: episodes, anomalous minutes, **anomalous share** (the default, since it normalises for added hosts and gaps), peak severity, and affected entities.
- The day bars are the day buttons (height relative to the busiest day; the busiest day is highlighted). A headline states the detector's direction, with the last-7-days vs previous-7-days share and episode counts beside it.
- Each bucket also shows coverage as a thin strip under the axis. A bucket without enough baseline is hatched and labelled "insufficient baseline" instead of being drawn as zero.
- Clicking a bucket (or pressing Enter on it) lists that day's episodes. Each episode links to its evidence detail if it lies in the latest day, and otherwise shows a stored summary (trend episodes from earlier days keep summaries, not raw series; see DECISIONS §6).
- Filters: entity, category.
- A **Recurring** table lists entities/categories with episodes on ≥ 3 of the 14 days.
- Every chart has a "Show data" table alternative.

## 9. Responsive and keyboard behaviour

- Breakpoints: ≥ 1200 px shows the list and the detail side by side. At 900–1199 px the detail panel overlays the list. Below 900 px the detail is a full-width route. Nothing scrolls horizontally except the data tables, which have their own scroll container.
- Keyboard:
  - Tab order runs header, then tabs, then filters, then content.
  - Tabs follow the WAI-ARIA tabs pattern (arrow keys).
  - Finding rows are links. `j`/`k` move between findings and `Esc` closes the detail on desktop. Shortcuts are listed under `?`, and none of them fire inside inputs.
  - Focus moves to the detail heading when the panel opens and returns to the row when it closes.
- Visible focus rings, colour contrast ≥ 4.5:1 for text, and support for `prefers-reduced-motion` and `prefers-color-scheme`.
- **Theme**: an Auto / Light / Dark switch in the app header. Auto follows `prefers-color-scheme`; an explicit choice is stored per viewer (`localStorage`, key `assistant.theme`) and applied as `data-theme` on `<html>`. Colours are tokens in `frontend/src/styles/tokens.css`; charts use the matching palette from `frontend/src/lib/theme.ts`.

## 10. Global loading, error, and partial states

| Situation | Behaviour |
| --- | --- |
| Report loading | Header skeleton, then content. The previous report is never shown under a new URL. |
| Report 404 `report_unavailable` | A message saying why (failed or cancelled), with *Run again*. |
| `schema_unsupported` | "This report was saved by an incompatible version." Scope and date are still shown from the job record. |
| Backend unreachable | A full-page error with Retry. Nothing is cached as if it were fresh. |
| Partial report | An amber banner, plus a Source error status on the affected coverage rows. |

## 11. Workflow walkthrough (synthetic scenarios)

| Scenario | Path through the UI | Expected presentation |
| --- | --- | --- |
| **Healthy data** (paas/production, 14 d history) | Start → Analyze → progress → Overview | 0 findings, "No anomalies detected in evaluated signals", full coverage table, Trends with 14 bars at 0 % anomalous share and full coverage. AI skipped (`skipped_no_findings`). |
| **Sustained CPU load** (`node_cpu_seconds_total` idle drops, 95 % for 3 h on `paas-production`) | Overview top finding → Evidence | High/critical "CPU utilisation" on `node · paas-production`: observed 95 % vs expected 22 % (band), a 3 h span, the absolute heuristic line at 90 %, confidence high. The AI hypothesis links to it. Trends day 14 shows the spike. |
| **404 increase** (`dispatcher-api`, `GET /api/v3/tasks/:task` 404s ×6, 5xx flat) | Findings → filter category "Client errors" | "404 increase" (severity ≤ medium). The 5xx row reads "No anomaly". `error_type="404"` is shown as a label, not as a failure. No host attribution to `paas-production`. |
| **Missing histogram buckets** (only `_count`) | Overview → Coverage | Latency row is *Unsupported*: "No histogram buckets or `_sum` for `http_server_request_duration_seconds`". Traffic and status findings still appear. No latency chart. |
| **Short retention** (5 days of history) | Overview → Trends | Latest-day findings have confidence medium (4 baseline days). Trend buckets with < 3 baseline days (the oldest observed days) are hatched "insufficient baseline", buckets before the history starts show "insufficient data", and the rest show their reduced baseline length. The summary shows "Baseline: 4 days". |
| **AI failure** (OpenAI timeout) | Overview | All findings and evidence render. The explanation panel reads "could not be generated (timeout)". Job state is `completed`, explanation status `failed`. |
| **Reopen saved report** (after restart) | Start → Recent reports row → `/reports/:id` | The same scope, `T`, detector version, findings, and evidence charts load from SQLite without querying the metrics source. A job running at restart time appears as `failed · interrupted_by_restart`. |

These cover product workflows 1 (investigate today), 2 (assess deterioration: Trends, Recurring, bucket drill-down), 3 (incomplete telemetry: missing histograms, short retention), 4 (without AI), and 5 (revisit).
