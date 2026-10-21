# UI specification (T001)

Status: recorded 2026-09-30 by T001. Layouts, wording, and styling are **implementation defaults**, not owner-confirmed requirements. The confirmed parts are those in the [product plan](PRODUCT_PLAN.md#confirmed-requirements): a web UI, one project/env per analysis, the latest-day analysis, the two-week trend, and OpenAI-first explanations. Technical choices are in [DECISIONS.md](DECISIONS.md).

Interface language: English (default; strings are kept in one module so they can be translated later).

## 1. Routes

| URL | View |
| --- | --- |
| `/` | **Projects**: one card per project with health, latest report, 14-day trend and Run (T013) |
| `/projects/new`, `/projects/new?from=:projectId`, `/projects/:projectId/edit` | **Project form**: labels, source, Test connection, clone, delete |
| `/projects/:projectId` | **Project**: configuration, Run, analysis history |
| `/credentials` | **Credentials**: the secrets project sources authenticate with (T021) |
| `/analyses/:id` | **Job progress**; switches to the report route when a report exists |
| `/reports/:id` | **Overview** (default report tab) |
| `/reports/:id/findings` | **Findings** list; `?category=&severity=&entity=` filters live in the URL |
| `/reports/:id/findings/:findingId` | Findings list + **Evidence** detail (a full-width page on narrow screens) |
| `/reports/:id/timeline` | **Timeline**: the full state timeline; `?source=` (comma-separated source kinds, absent = all) |
| `/reports/:id/trends` | **Trends**; `?entity=&category=` filters |
| `/reports/:id/scope` | **Scope**: what each source's filter selected (T022) |

Every report URL can be shared and reloaded and works after a process restart, because it is read from the saved snapshot. The browser Back button moves between the list and the detail view without losing filters.

## 2. App shell and persistent report frame

Visual design: the "DevOps Assistant UI" canvas in Claude Design (Overview, Findings + evidence, Trends boards). It is a dense, Grafana-like dashboard: IBM Plex Sans/Mono (self-hosted via `@fontsource`, no external font requests), panels on a 24-column grid, 4 px radii, solid severity fills for stat panels and chips.

Every page has a left **icon rail** (home, Projects, and Overview / Findings / Timeline / Trends / Scope while a report is open; a top row below 700 px) and a **top bar** with breadcrumbs (`Projects › <project> › <page>`), page badges such as *Synthetic data*, and the theme switch.

Report routes add a toolbar and tabs under the top bar:

```
[env | production] [project | shop] [baseline | 14 days]   Completed · detectors-2026.09.1 · saved 30 Sep 10:07 UTC   [◷ 29 Sep 10:05 – 30 Sep 10:05 | UTC ▾] [Run again]
Tabs:  Overview | Findings 12 | Timeline | Trends | Scope 1
```

- The project's label matchers, the longest baseline used, and the frozen latest-day window are always visible. Times are shown in the viewer's local time by default, with a Local/UTC select next to the window; tooltips always include UTC.
- The project name in the breadcrumbs links to its project page. *Run again* uses the same project with `T = now`.
- A `partial` report shows a persistent amber banner ("Some signals could not be collected — see Coverage"), with a link to the coverage section.

## 3. Projects (T013)

Replaces the original start view (project/env selectors). Screenshots: `docs/screenshots/projects-running-desktop.png`, `projects-narrow.png`, `project-form-desktop.png`, `project-form-narrow.png`, `project-detail-desktop.png`, `project-delete.png`.

**List (`/`).** One card per project, sorted by name:

- Name (links to the project page), label matchers as chips (when set), one line per data source ("Prometheus · vm:8428", "Cloudflare · shop.example.com", "Sentry · acme: shop-api, shop-web · production", "Wazuh · wazuh-indexer:9200", "Grafana · grafana.example.com"; `synthetic: <scenario>` for demo sources), and the schedule when one is set ("Scheduled: Daily at 08:00 (Europe/Berlin)").
- **Source health**: fetched lazily per project from `GET /api/projects/{id}/health` (one test per source, cached 60 s); the worst source wins and the tooltip lists each. States: *Reachable*, *Unreachable*, *Auth failed*, *No matching series* (Prometheus), *No traffic* (Cloudflare, no requests in 24 h; a Sentry project without events or a Wazuh agent without alerts is not a warning), *No source*, *Credentials unreadable*, *Checking…*. Projects that cannot be checked (no source, unreadable credentials) are never probed. Text and icon carry the state; colour is never the only signal.
- **Latest report**: state and relative age (UTC tooltip), linking to the report or, without one, to the job. Shows severity counts, or the error code for a failed run.
- **14-day anomalies**: an SVG bar sparkline of `daily_episodes` (oldest first; the latest day is emphasised; days without enough data are dashed stubs). Its accessible name summarises the total and the latest day. "No trend yet" without a report.
- **Run analysis**: posts `{project_id}`. While a job is queued or running, the card shows "Running · <stage> (done/total)" linked to the job instead of the button, and the list polls every 2 s. `queue_full` shows the Retry-After time inline. Without a source, or with unreadable credentials, the button is disabled with a reason and an Edit link.
- Empty state: an explanation and a *Create project* call to action. Load errors show a Retry button. A text filter (name or `label=value`) appears once there are more than 10 projects.

**Project page (`/projects/:projectId`).** Name, description, Prometheus (host, auth type, TLS note, matchers, custom rules with how each decides), Cloudflare (zone, hostnames), Sentry (organization and projects, environment, tag filters as chips, non-default URL), Wazuh (indexer host, agents and "group <name>" entries, or "agents by label", agent labels as chips, non-default index pattern, TLS note), Grafana alerts (host, "folder <name>"/"rule <uid>" entries or "rules by label", non-default organization, rule and instance labels as chips, TLS note), health, schedule with the next run ("On demand only" without one), saved report count (with "· latest N kept" when retention is set), Edit and Run. The analysis history pages through `GET /api/analyses?project_id=` (20 per page, *Load older analyses*); scheduled runs are marked "· scheduled". The report and job headers link back here.

**Form (`/projects/new`, `/projects/new?from=:projectId`, `/projects/:projectId/edit`).**

- **Clone** (project page → *Clone*, `?from=`): the new-project form prefilled with every setting of the original (labels, source, schedule, retention) and the name "<name> (copy)". The clone uses the same credentials. Reports are not copied.

- Name and optional description.
- **Prometheus-compatible metrics** — **Labels**: rows of `name <op> value`, with an operator select (`=`, `!=`, `=~`, `!~`; default `=`) (add/remove, up to 10; at least one once a URL is set, and rows without a value are ignored while there is no URL). Client validation mirrors the server: label name syntax, reserved `__` prefix, duplicates, required values, and at least one `=`/`=~` row; regex syntax (RE2) and regexes matching an empty value are checked by the server (shown on the row and under the labels respectively). URL (no credentials, query or fragment; `synthetic://<scenario>` allowed; empty means no source), TLS verification, and its credential (none, or a Prometheus credential with bearer or basic auth).
- **Credential of every source** (T021): sources hold no secrets.
  - Each source has a *Credential* select listing that kind's credentials. *None* is offered for Prometheus and Wazuh, and for synthetic:// sources of the other kinds. Otherwise *Choose a credential* is required.
  - *New credential…* opens an inline form: credential name, plus the kind's mechanism and secret. Prometheus offers bearer or basic, Cloudflare an API token, Sentry an auth token, Wazuh a username and password, Grafana a service account token.
  - *Create credential* saves the credential (other projects can use it too) and selects it.
- **Cloudflare**: *Analyse a Cloudflare zone* reveals Zone ID (32 hex), optional hostnames (comma/space separated, normalised to lower case), its credential (not needed for `synthetic://`), and an *Advanced* API URL. Server `422` errors on `sources.<i>` map back to the source they belong to.
- **Sentry**: *Analyse a Sentry project* reveals Organization, *Sentry projects* (up to 10 slugs, comma/space separated, normalised to lower case), an optional Environment (empty = all), optional *Tag filters* as `key = value` rows (add/remove, up to 10; key syntax, duplicates and required values are checked; completely empty rows are ignored), its credential (not needed for `synthetic://`), and an *Advanced* Sentry URL (`https://de.sentry.io` for EU organizations, or self-hosted).
- **Wazuh**: *Analyse Wazuh agents* reveals the Indexer URL, TLS verification, *Agents* (up to 50 names, comma/space separated, case kept), *Agent groups* (up to 10, resolved from the dashboard's monitoring index; a *Monitoring index pattern* field appears once a group is entered), *Agent labels* as `key = value` rows (up to 10; at least one agent, group or label is required), the alerts index pattern, and its credential (none when the indexer needs no authentication).
- **Grafana alerts**: *Analyse Grafana alert rules* reveals the Grafana URL, TLS verification, *Organization ID*, *Folders* (up to 20 titles or UIDs, comma separated; titles may contain spaces), *Rule UIDs* (up to 100), *Rule labels* and *Instance labels* as `name <op> value` rows with the same operator select (up to 10 each; at least one folder, rule UID or rule label is required), and its credential (not needed for `synthetic://`).
- **Custom rules** (under the Prometheus connection, once a URL is set; T020): a list of rules, each collapsed to its title, identifier and how it decides ("Anomaly (increases)", "≥ 500 for 10 min"), with *Edit* and *Remove*; invalid rules show *Needs attention*. Editing reveals Title (the identifier follows it until edited by hand), Identifier, *What it means*, *PromQL query* (the project's labels are added to every selector; `$__window` is the rate window), Unit, *Finding when* (Anomaly / Threshold / Anomaly or threshold / Event), the direction for anomalies, *Breached at or above/below* with Threshold, optional Critical and *For at least (min)* for thresholds, and under *Advanced* the minimum change factor and change, the highest (or, for events, the) severity and identity labels. *Preview last 24 h* runs the rule on the draft (stored secrets reused) and shows the query as run and up to 20 series with their latest value and a sparkline with the threshold line; editing the rule afterwards marks the preview as earlier. Up to 20 rules; editing rules does not make the connection test stale.
- **Test Prometheus connection** / **Test Cloudflare connection** / **Test Sentry connection** / **Test Wazuh connection** / **Test Grafana connection** run per source on the unsaved draft (with its selected credential) and show reachability/auth, matching series (Prometheus) requests in the last 24 h (Cloudflare) error events plus transactions in the last 24 h (Sentry) alerts of the selected agents in the last 24 h (Wazuh) or current alert instances matching the instance labels (Grafana), history days, and a capability table per signal family. Editing a source after its test marks the result stale. Saving is never blocked by tests, but a missing, stale or failed test of any configured source (or no source at all) shows a warning next to Save.
- **Schedule**: *Generate a report automatically* reveals a time (`HH:MM`), a time zone (IANA name with suggestions; defaults to the browser's zone) and weekday checkboxes (all checked by default; at least one required). Server `422` errors (e.g. an unknown time zone) map back to the field. Without a source a note says scheduled runs are skipped.
- **Report retention**: *Keep only the latest reports* reveals *Reports to keep* (1–1000, default 10). Older analyses and their reports are deleted automatically after each analysis; saving a lower number deletes the excess right away.
- **Delete** (edit only): states how many saved reports will be removed, is disabled while an analysis is queued or running, and requires typing the project name before *Delete permanently* is enabled.

### Credentials (T021)

*Credentials* in the rail opens `/credentials`.
- **Table**: name (with description), source kind, mechanism (with the basic-auth username), the projects using it (links), and when it was last updated.
- ***New credential* / *Edit***: an inline form with name, optional description, source kind and the kind's secret mechanisms (the same fields as *New credential…* in a project's source).
  - On edit, an empty secret keeps the stored one ("Stored — leave empty to keep").
  - The kind is locked while the credential is in use.
- ***Delete…***: asks for confirmation; a credential in use shows the server's reason, naming the projects.
- **Unreadable**: a credential that cannot be decrypted says so in its row.
- **Empty state**: points to *New credential…* in a project's source.

## 4. Job progress view

- The stage list (`discovery → collection → detection → trends → explanation → saving`) shows a status icon, a text label, counts (e.g. "collection 38/52 queries"), and elapsed time. It is an `aria-live="polite"` region that announces stage changes only.
- A **Cancel** button (confirmed inline, not with a browser dialog) calls `DELETE`. The Cancelled state offers *Run again*.
- Failed state: the error `code` and a human message. `interrupted_by_restart` reads "The service restarted while this analysis was running. No report was saved." Both show *Run again*.
- The view polls every 2 s. After 3 consecutive network errors it shows "Lost connection to the assistant — retrying" and keeps retrying with backoff.

## 5. Overview

Order (top to bottom). The page answers "what is wrong, how bad, and what is new" before any detail:

1. **Stat panels**: one per severity (Critical, High, Medium always; Low when present), filled with the severity colour when the count is above zero, with the family and new/seen-before note; *New today*; *Signal coverage* (evaluated of total families, with a bar); *Blind spots* (count and family names). Below them, one **event total** panel per Sentry, Wazuh and Grafana source (sharing one row): the latest 24 h's error events, alerts or alert firings (instances firing at any time in the day) over every analysed project, agent or rule, compared with the median of the earlier days, and 14 day bars (the latest highlighted, unknown days hatched). A partly known latest day shows "≥" and "partial data".
2. **Summary**: a one-line headline of the most severe finding ("CPU on node · shop-production peaked at 95.4 % over 3 h"), its ratio to the usual value, how many findings are new today versus seen before, a link to the most severe finding, the 14-day trend line, and a warning box naming the families that were not evaluated (unsupported, insufficient data, source error) and saying that problems there would not show up. Beside it, **Check first** (the AI explanation panel, below).
3. **Episodes** (a collapsible row): a compact **State timeline** with one lane per finding (at most 8, with a link to the Timeline tab when there are more) on the latest-day axis; the lane background is neutral ("No episode"), never "normal", because it does not prove the entity was healthy. Findings that overlap in time are listed as "coincide in time (not established as related)". Below it, the 6 most severe findings as panels: severity, title, New today / Recurring tag, the observed peak with the usual range (or the heuristic), the finding's summary (at most two lines), a mini evidence chart with axes, episode span, heuristic line and usual band, the series legend, then time, duration, state and confidence. A "View all findings" link.
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

- A grid table in one panel (it scrolls horizontally inside its container on narrow screens). Columns: severity, finding, entity (e.g. `node · shop-production` or `checkout-api · GET /api/v1/orders/:order`; Grafana findings add their alert instances, e.g. `HostRebooted · gpu-hive-4, gpu-lndvps-12 +1`; a Sentry project's error findings add the error with the most events, e.g. `shop-api (production) · KeyError: 'currency' +3`), started, when (a strip of the analysed 24 hours ending at the report's end time, with the episode marked on it, and how long before that end it ran, e.g. `19 h → 4 h ago`, `3 h ago → now` or `45 min → 20 min ago`), duration, peak, usual range (or heuristic), confidence, and recurrence state. The open finding's row is highlighted.
- Default sort is severity, then peak time. Filters are labelled controls (Category, Severity, Entity), and filter state lives in the URL. The Entity filter also matches Grafana alert instance label values and Sentry error titles and issue IDs, so a host name finds its alerts and an error message its spikes. A **Recurrence** toggle (All / New today / Happened before, `?recurrence=new|seen`) sits on the left of the filter row.
- 4xx findings are titled "Client error increase (4xx)" or "404 increase". 5xx findings are titled "Server error rate (5xx)". The two never share a label.
- An empty list after filtering shows "No findings match these filters" and a Clear filters button.

## 7. Evidence detail

Opening a finding expands it in place, accordion-style: a detail row directly below the finding's row holds three panels, and clicking the row (or its title) again collapses it. Only one finding is open at a time. The panels are the evidence chart (16 of 24 columns), *Finding details* (8 columns) and a full-width *Query inspector*. Below 1100 px they stack. Contents:

1. Title, severity, recurrence, confidence with its **reasons** (e.g. "4 baseline days (< 7)", "coverage 82 %"), state, peak / usual / duration stats, window, detector version and baseline. Under the headline, the finding's **summary** (what the problem is), and below the stats a **What to check** box with its help text (both from the rule; custom rule findings show the rule's description instead).
2. **Chart** (ECharts, SVG renderer):
   - The observed series and the expected band (median ± scaled MAD, or the absolute threshold line labelled as a heuristic).
   - The shaded episode span. Gaps are drawn as breaks, and a "gap" marker appears in the tooltip.
   - A y-axis with a unit (%, req/s, bytes/s, s) and a UTC time axis.
   - Keyboard-accessible dataZoom, plus a generated `aria` description.
3. A **"Show data" toggle** that renders the same points as a table (time, observed, expected, lower, upper, gap flag). This is the chart alternative.
4. **AI hypotheses referencing this finding**, if any, each labelled unverified.
4a. **Alert instances** (Grafana findings, full-width panel above the Query inspector): each instance active during the finding, named by the labels that differ between the instances (or its `instance`/`host`-like label when alone), with its active intervals ("– still firing" when open), its annotations (URLs as links; the rule's summary appears once in *Finding details* instead), and every label as a chip, the distinguishing ones emphasised. Ten are shown, then *Show all N*.
4b. **Errors** (Sentry project error findings, full-width panel above the Query inspector): each error kind with events during the finding — title, issue short ID (a link to the issue in Sentry for a real source), events and share, most events first (`(other errors)` reads *Other errors*).
4c. **Custom rules** (findings of family *Custom rules*, T020): *Finding details* lead with the rule's description and list *Custom rule* (title and how it decides, from the copy kept in the report); its threshold reads "Threshold … (set in the custom rule)", never "diagnostic, not an SLO". The Query inspector shows the rule's query with the project's labels injected.
5. **Query inspector**: the exact PromQL/MetricsQL, step, and range in a copyable code block (credentials are never shown), and a collapsed *Labels and detector* section with the relevant entity labels (job, instance, route, method, status, device, mountpoint).

Unsupported or insufficient signals have no chart. They show a text block explaining what metric or label is missing, or how much history exists.

## 7a. Timeline

- The full **State timeline** (same lanes, axis, legend and "coincide in time" notes as the Overview one) with a lane for every latest-day finding, sorted by start time; each lane also names its source (icon). Coinciding pairs beyond 8 are summarised as a count.
- A **Sources** toggle group above it: *All · N* plus one toggle per source kind that produced findings (icon, name, count). Every source is on by default; toggles combine, and *All* turns every source back on. The selection lives in `?source=` (absent when all are on). Turning every source off shows "No sources selected" with *Show all sources*.
- A report without findings says there are no episodes to place on the timeline.

## 8. Trends

- **Daily measures** over 14 consecutive buckets (labelled by bucket end date, UTC), with a series selector: **anomalous share** (the default, since it normalises for added hosts and gaps), episodes, anomalous entity-minutes, and affected entities.
- The day bars are the day buttons (height relative to the busiest day). The latest day is drawn in the critical fill and earlier days in amber; the busiest day's value is bold. A Summary panel states the detector's direction, with stat panels for the last-7-days vs previous-7-days share and episode counts (▲ red when higher, ▼ green when lower).
- Each bucket also shows coverage as a thin strip under the axis. A bucket without enough baseline is hatched and labelled "insufficient baseline" instead of being drawn as zero.
- The latest day is selected by default (`?day=` selects another). Clicking a bucket (or pressing Enter on it) lists that day's episodes. Each episode links to its evidence detail if it lies in the latest day, and otherwise shows a stored summary (trend episodes from earlier days keep summaries, not raw series; see DECISIONS §6).
- Filters: entity, category.
- **Recurring problems** lists entity/signal pairs with episodes on ≥ 3 of the 14 days as a 14-cell status history: latest-day episode (red), earlier episode (amber), evaluated without an episode (dim green), no baseline or data (hatched).
- Every chart has a "Show data" table alternative.

## 8a. Scope (T022)

- One panel per analysed source, in report order: icon, name, base URL and the snapshot's summary (e.g. "4 of 5 rules, 5 current instances").
- **Filter checks** table: filter part (kind and value), *Matched*, *Without this part* (— for listed items), the values that exist in the source as chips (the ones the part accepts highlighted, "and more" when cut) and a verdict: **Excludes all data** (matches nothing, but dropping it finds data), **No such value** (the values exist but none is accepted), **Matches nothing**, **No effect** (dropping it changes nothing) or OK. The first three are in the critical colour, and the tab counts them ("Scope 1").
- **Selected** (largest first, "showing 50 of N" when capped), **Samples** (label sets) and **Requests** (copy per request) follow as disclosures; notes such as `nothing_selected` or `diagnostic_failed` are listed above the table.
- A source that failed shows its error instead. Reports before schema 2.8 say they predate scope diagnostics and suggest a new analysis.

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
