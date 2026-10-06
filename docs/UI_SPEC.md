# UI specification (T001)

Status: recorded 2026-09-30 by T001. Layouts, wording, and styling are **implementation defaults**, not owner-confirmed requirements. The confirmed parts are those in the [product plan](PRODUCT_PLAN.md#confirmed-requirements): a web UI, one project/env per analysis, the latest-day analysis, the two-week trend, and OpenAI-first explanations. Technical choices are in [DECISIONS.md](DECISIONS.md).

Interface language: English (default; strings are kept in one module so they can be translated later).

## 1. Routes

| URL | View |
| --- | --- |
| `/` | **Projects**: one card per project with health, latest report, 14-day trend and Run (T013) |
| `/projects/new`, `/projects/new?from=:projectId`, `/projects/:projectId/edit` | **Project form**: labels, source, Test connection, clone, delete |
| `/projects/:projectId` | **Project**: configuration, Run, analysis history |
| `/analyses/:id` | **Job progress**; switches to the report route when a report exists |
| `/reports/:id` | **Overview** (default report tab) |
| `/reports/:id/findings` | **Findings** list; `?category=&severity=&entity=` filters live in the URL |
| `/reports/:id/findings/:findingId` | Findings list + **Evidence** detail (a full-width page on narrow screens) |
| `/reports/:id/trends` | **Trends**; `?entity=&category=` filters |

Every report URL can be shared and reloaded and works after a process restart, because it is read from the saved snapshot. The browser Back button moves between the list and the detail view without losing filters.

## 2. App shell and persistent report frame

Visual design: the "DevOps Assistant UI" canvas in Claude Design (Overview, Findings + evidence, Trends boards). It is a dense, Grafana-like dashboard: IBM Plex Sans/Mono (self-hosted via `@fontsource`, no external font requests), panels on a 24-column grid, 4 px radii, solid severity fills for stat panels and chips.

Every page has a left **icon rail** (home, Projects, and Overview / Findings / Trends while a report is open; a top row below 700 px) and a **top bar** with breadcrumbs (`Projects › <project> › <page>`), page badges such as *Synthetic data*, and the theme switch.

Report routes add a toolbar and tabs under the top bar:

```
[env | production] [project | shop] [baseline | 14 days]   Completed · detectors-2026.09.1 · saved 30 Sep 10:07 UTC   [◷ 29 Sep 10:05 – 30 Sep 10:05 | UTC ▾] [Run again]
Tabs:  Overview | Findings 12 | Trends
```

- The project's label matchers, the longest baseline used, and the frozen latest-day window are always visible. Times are shown in the viewer's local time by default, with a Local/UTC select next to the window; tooltips always include UTC.
- The project name in the breadcrumbs links to its project page. *Run again* uses the same project with `T = now`.
- A `partial` report shows a persistent amber banner ("Some signals could not be collected — see Coverage"), with a link to the coverage section.

## 3. Projects (T013)

Replaces the original start view (project/env selectors). Screenshots: `docs/screenshots/projects-running-desktop.png`, `projects-narrow.png`, `project-form-desktop.png`, `project-form-narrow.png`, `project-detail-desktop.png`, `project-delete.png`.

**List (`/`).** One card per project, sorted by name:

- Name (links to the project page), label matchers as chips (when set), one line per data source ("Prometheus · vm:8428", "Cloudflare · shop.example.com", "Sentry · acme: shop-api, shop-web · production"; `synthetic: <scenario>` for demo sources), and the schedule when one is set ("Scheduled: Daily at 08:00 (Europe/Berlin)").
- **Source health**: fetched lazily per project from `GET /api/projects/{id}/health` (one test per source, cached 60 s); the worst source wins and the tooltip lists each. States: *Reachable*, *Unreachable*, *Auth failed*, *No matching series* (Prometheus), *No traffic* (Cloudflare, no requests in 24 h; a Sentry project without events is not a warning), *No source*, *Credentials unreadable*, *Checking…*. Projects that cannot be checked (no source, unreadable credentials) are never probed. Text and icon carry the state; colour is never the only signal.
- **Latest report**: state and relative age (UTC tooltip), linking to the report or, without one, to the job. Shows severity counts, or the error code for a failed run.
- **14-day anomalies**: an SVG bar sparkline of `daily_episodes` (oldest first; the latest day is emphasised; days without enough data are dashed stubs). Its accessible name summarises the total and the latest day. "No trend yet" without a report.
- **Run analysis**: posts `{project_id}`. While a job is queued or running, the card shows "Running · <stage> (done/total)" linked to the job instead of the button, and the list polls every 2 s. `queue_full` shows the Retry-After time inline. Without a source, or with unreadable credentials, the button is disabled with a reason and an Edit link.
- Empty state: an explanation and a *Create project* call to action. Load errors show a Retry button. A text filter (name or `label=value`) appears once there are more than 10 projects.

**Project page (`/projects/:projectId`).** Name, description, Prometheus (host, auth type, TLS note, matchers), Cloudflare (zone, hostnames), Sentry (organization and projects, environment, tag filters as chips, non-default URL), health, schedule with the next run ("On demand only" without one), saved report count (with "· latest N kept" when retention is set), Edit and Run. The analysis history pages through `GET /api/analyses?project_id=` (20 per page, *Load older analyses*); scheduled runs are marked "· scheduled". The report and job headers link back here.

**Form (`/projects/new`, `/projects/new?from=:projectId`, `/projects/:projectId/edit`).**

- **Clone** (project page → *Clone*, `?from=`): the new-project form prefilled with every setting of the original (labels, source, schedule, retention) and the name "<name> (copy)". Stored secrets are reused like in edit mode — for Test connection and, via `POST /api/projects?clone_of=<id>`, copied into the new project. Reports are not copied.

- Name and optional description.
- **Prometheus-compatible metrics** — **Labels**: rows of `name = value` (add/remove, up to 10; at least one once a URL is set, and rows without a value are ignored while there is no URL). Client validation mirrors the server: label name syntax, reserved `__` prefix, duplicates, required values. URL (no credentials, query or fragment; `synthetic://<scenario>` allowed; empty means no source), TLS verification, and authentication (none, bearer, basic). Secret inputs are write-only. When a secret of the chosen type is stored, the placeholder reads "Stored — leave empty to keep" and an empty input keeps it. Switching the type requires the new secret.
- **Cloudflare**: *Analyse a Cloudflare zone* reveals Zone ID (32 hex), optional hostnames (comma/space separated, normalised to lower case), the write-only API token ("Stored — leave empty to keep"; not needed for `synthetic://`), and an *Advanced* API URL. Server `422` errors on `sources.<i>` map back to the source they belong to.
- **Sentry**: *Analyse a Sentry project* reveals Organization, *Sentry projects* (up to 10 slugs, comma/space separated, normalised to lower case), an optional Environment (empty = all), optional *Tag filters* as `key = value` rows (add/remove, up to 10; key syntax, duplicates and required values are checked; completely empty rows are ignored), the write-only Auth token ("Stored — leave empty to keep"; not needed for `synthetic://`), and an *Advanced* Sentry URL (`https://de.sentry.io` for EU organizations, or self-hosted).
- **Test Prometheus connection** / **Test Cloudflare connection** / **Test Sentry connection** run per source on the unsaved draft (stored secrets are reused in edit and clone mode) and show reachability/auth, matching series (Prometheus) requests in the last 24 h (Cloudflare) or error events plus transactions in the last 24 h (Sentry), history days, and a capability table per signal family. Editing a source after its test marks the result stale. Saving is never blocked by tests, but a missing, stale or failed test of any configured source (or no source at all) shows a warning next to Save.
- **Schedule**: *Generate a report automatically* reveals a time (`HH:MM`), a time zone (IANA name with suggestions; defaults to the browser's zone) and weekday checkboxes (all checked by default; at least one required). Server `422` errors (e.g. an unknown time zone) map back to the field. Without a source a note says scheduled runs are skipped.
- **Report retention**: *Keep only the latest reports* reveals *Reports to keep* (1–1000, default 10). Older analyses and their reports are deleted automatically after each analysis; saving a lower number deletes the excess right away.
- **Delete** (edit only): states how many saved reports will be removed, is disabled while an analysis is queued or running, and requires typing the project name before *Delete permanently* is enabled.

## 4. Job progress view

- The stage list (`discovery → collection → detection → trends → explanation → saving`) shows a status icon, a text label, counts (e.g. "collection 38/52 queries"), and elapsed time. It is an `aria-live="polite"` region that announces stage changes only.
- A **Cancel** button (confirmed inline, not with a browser dialog) calls `DELETE`. The Cancelled state offers *Run again*.
- Failed state: the error `code` and a human message. `interrupted_by_restart` reads "The service restarted while this analysis was running. No report was saved." Both show *Run again*.
- The view polls every 2 s. After 3 consecutive network errors it shows "Lost connection to the assistant — retrying" and keeps retrying with backoff.

## 5. Overview

Order (top to bottom). The page answers "what is wrong, how bad, and what is new" before any detail:

1. **Stat panels**: one per severity (Critical, High, Medium always; Low when present), filled with the severity colour when the count is above zero, with the family and new/seen-before note; *New today*; *Signal coverage* (evaluated of total families, with a bar); *Blind spots* (count and family names).
2. **Summary**: a one-line headline of the most severe finding ("CPU on node · shop-production peaked at 95.4 % over 3 h"), its ratio to the usual value, how many findings are new today versus seen before, a link to the most severe finding, the 14-day trend line, and a warning box naming the families that were not evaluated (unsupported, insufficient data, source error) and saying that problems there would not show up. Beside it, **Check first** (the AI explanation panel, below).
3. **Episodes** (a collapsible row): a **State timeline** with one lane per finding (at most 8) on the latest-day axis; the lane background is neutral ("No episode"), never "normal", because it does not prove the entity was healthy. Findings that overlap in time are listed as "coincide in time (not established as related)". Below it, the 6 most severe findings as panels: severity, title, New today / Recurring tag, the observed peak with the usual range (or the heuristic), a mini evidence chart with axes, episode span, heuristic line and usual band, the series legend, then time, duration, state and confidence. A "View all findings" link.
4. **Coverage** (a collapsible row): a **Signal families** tile grid (one solid tile per family: red anomalous, amber not evaluated, green no anomaly; each with text and icon), then the coverage table (5).
5. **Check first** contents:
   - `succeeded`: the summary. Hypotheses are labelled **"Hypothesis — unverified"** with their likelihood, and each links to its referenced finding chips. Investigation steps follow, then the uncertainty note and "Generated by <provider>/<model>".
   - `disabled` / `not_configured`: "AI explanations are off. The numerical report is complete." (For `not_configured`, a hint names the missing variable.)
   - `failed`: "The explanation could not be generated (<reason>). Findings and evidence below are unaffected."
   - `skipped_no_findings`: no panel. The summary strip already says so.
6. **Coverage and limitations**: a table per signal family (CPU, Memory, Filesystem, Disk I/O, Network, Containers, HTTP/RPC traffic, Failures, Client errors, Latency). Each row has a status, a reason, and the baseline days used. The report's `exclusions` (budget truncation, dropped evidence) are listed here.
7. **Healthy report**: "No anomalies detected in evaluated signals" is shown **only together with** the coverage table, never on its own. If nothing could be evaluated, it says "No signals could be evaluated" instead of claiming health.

### Status vocabulary (text + icon + colour; colour never used alone)

| Status | Meaning | Icon |
| --- | --- | --- |
| Anomalous | ≥ 1 finding | filled triangle |
| No anomaly | Evaluated with adequate coverage, no finding | check |
| Insufficient data | Coverage or baseline below minimum; relative detection unavailable | half-circle |
| Unsupported | Source metrics/labels absent (e.g. no histogram → no latency) | slashed circle |
| Source error | Query failed, timed out, or was truncated | exclamation |
| Not evaluated | Out of scope for this detector version | dash |

Severity chips: Critical / High / Medium / Low, each with a text label (solid fills; Low is outlined). Confidence is shown as a separate text badge ("Confidence: medium") so it is never merged with severity. Thresholds are described as "diagnostic heuristic", never "SLO".

## 6. Findings

- A grid table in one panel (it scrolls horizontally inside its container on narrow screens). Columns: severity, finding, entity (e.g. `node · shop-production` or `checkout-api · GET /api/v1/orders/:order`), started, duration, peak, usual range (or heuristic), confidence, and recurrence state. The open finding's row is highlighted.
- Default sort is severity, then peak time. Filters are labelled controls (Category, Severity, Entity), and filter state lives in the URL. A **Recurrence** toggle (All / New today / Happened before, `?recurrence=new|seen`) sits on the left of the filter row.
- 4xx findings are titled "Client error increase (4xx)" or "404 increase". 5xx findings are titled "Server error rate (5xx)". The two never share a label.
- An empty list after filtering shows "No findings match these filters" and a Clear filters button.

## 7. Evidence detail

Opening a finding expands it in place, accordion-style: a detail row directly below the finding's row holds three panels, and clicking the row (or its title) again collapses it. Only one finding is open at a time. The panels are the evidence chart (16 of 24 columns, with a close button), *Finding details* (8 columns) and a full-width *Query inspector*. Below 1100 px they stack. Contents:

1. Title, severity, recurrence, confidence with its **reasons** (e.g. "4 baseline days (< 7)", "coverage 82 %"), state, peak / usual / duration stats, window, detector version and baseline.
2. **Chart** (ECharts, SVG renderer):
   - The observed series and the expected band (median ± scaled MAD, or the absolute threshold line labelled as a heuristic).
   - The shaded episode span. Gaps are drawn as breaks, and a "gap" marker appears in the tooltip.
   - A y-axis with a unit (%, req/s, bytes/s, s) and a UTC time axis.
   - Keyboard-accessible dataZoom, plus a generated `aria` description.
3. A **"Show data" toggle** that renders the same points as a table (time, observed, expected, lower, upper, gap flag). This is the chart alternative.
4. **AI hypotheses referencing this finding**, if any, each labelled unverified.
5. **Query inspector**: the exact PromQL/MetricsQL, step, and range in a copyable code block (credentials are never shown), and a collapsed *Labels and detector* section with the relevant entity labels (job, instance, route, method, status, device, mountpoint).

Unsupported or insufficient signals have no chart. They show a text block explaining what metric or label is missing, or how much history exists.

## 8. Trends

- **Daily measures** over 14 consecutive buckets (labelled by bucket end date, UTC), with a series selector: **anomalous share** (the default, since it normalises for added hosts and gaps), episodes, anomalous entity-minutes, and affected entities.
- The day bars are the day buttons (height relative to the busiest day). The latest day is drawn in the critical fill and earlier days in amber; the busiest day's value is bold. A Summary panel states the detector's direction, with stat panels for the last-7-days vs previous-7-days share and episode counts (▲ red when higher, ▼ green when lower).
- Each bucket also shows coverage as a thin strip under the axis. A bucket without enough baseline is hatched and labelled "insufficient baseline" instead of being drawn as zero.
- The latest day is selected by default (`?day=` selects another). Clicking a bucket (or pressing Enter on it) lists that day's episodes. Each episode links to its evidence detail if it lies in the latest day, and otherwise shows a stored summary (trend episodes from earlier days keep summaries, not raw series; see DECISIONS §6).
- Filters: entity, category.
- **Recurring problems** lists entity/signal pairs with episodes on ≥ 3 of the 14 days as a 14-cell status history: latest-day episode (red), earlier episode (amber), evaluated without an episode (dim green), no baseline or data (hatched).
- Every chart has a "Show data" table alternative.

## 9. Responsive and keyboard behaviour

- Breakpoints: the 24-column grid keeps its layout above 1100 px. At 700–1100 px quarter-width stat panels go to a third and wider panels to full width. Below 700 px every panel is full width and the rail becomes a top row. Nothing scrolls horizontally except the data tables, which have their own scroll container.
- Keyboard:
  - Tab order runs header, then tabs, then filters, then content.
  - Tabs follow the WAI-ARIA tabs pattern (arrow keys).
  - Finding rows are links (the whole row is clickable) that toggle the expanded detail and expose `aria-expanded`. `j`/`k` move between findings and `Esc` closes the evidence. The shortcuts are listed under the table, and none of them fire inside inputs.
  - Focus moves to the detail heading when the panel opens and returns to the row when it closes.
- Visible focus rings, colour contrast ≥ 4.5:1 for text, and support for `prefers-reduced-motion` and `prefers-color-scheme`.
- **Theme**: an Auto / Light / Dark switch in the top bar. Auto follows `prefers-color-scheme`; an explicit choice is stored per viewer (`localStorage`, key `assistant.theme`) and applied as `data-theme` on `<html>`. Colours are tokens in `frontend/src/styles/tokens.css`; charts use the matching palette from `frontend/src/lib/theme.ts`.

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
| **Healthy data** (shop/production, 14 d history) | Start → Analyze → progress → Overview | 0 findings, "No anomalies detected in evaluated signals", full coverage table, Trends with 14 bars at 0 % anomalous share and full coverage. AI skipped (`skipped_no_findings`). |
| **Sustained CPU load** (`node_cpu_seconds_total` idle drops, 95 % for 3 h on `shop-production`) | Overview top finding → Evidence | High/critical "CPU utilisation" on `node · shop-production`: observed 95 % vs expected 22 % (band), a 3 h span, the absolute heuristic line at 90 %, confidence high. The AI hypothesis links to it. Trends day 14 shows the spike. |
| **404 increase** (`checkout-api`, `GET /api/v1/orders/:order` 404s ×6, 5xx flat) | Findings → filter category "Client errors" | "404 increase" (severity ≤ medium). The 5xx row reads "No anomaly". `error_type="404"` is shown as a label, not as a failure. No host attribution to `shop-production`. |
| **Missing histogram buckets** (only `_count`) | Overview → Coverage | Latency row is *Unsupported*: "No histogram buckets or `_sum` for `http_server_request_duration_seconds`". Traffic and status findings still appear. No latency chart. |
| **Short retention** (5 days of history) | Overview → Trends | Latest-day findings have confidence medium (4 baseline days). Trend buckets with < 3 baseline days (the oldest observed days) are hatched "insufficient baseline", buckets before the history starts show "insufficient data", and the rest show their reduced baseline length. The summary shows "Baseline: 4 days". |
| **AI failure** (OpenAI timeout) | Overview | All findings and evidence render. The explanation panel reads "could not be generated (timeout)". Job state is `completed`, explanation status `failed`. |
| **Reopen saved report** (after restart) | Start → Recent reports row → `/reports/:id` | The same scope, `T`, detector version, findings, and evidence charts load from SQLite without querying the metrics source. A job running at restart time appears as `failed · interrupted_by_restart`. |

These cover product workflows 1 (investigate today), 2 (assess deterioration: Trends, Recurring, bucket drill-down), 3 (incomplete telemetry: missing histograms, short retention), 4 (without AI), and 5 (revisit).
