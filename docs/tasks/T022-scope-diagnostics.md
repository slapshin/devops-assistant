# T022 — Scope diagnostics: what each source's filter actually selected

Dependencies: T021. Status: see [task index](README.md).

## Outcome

A report gets a **Scope** tab that shows, for every source of the project, what the project's filter selected during that run, and whether a wrong filter is the reason for missing or thin data. Today a filter that matches nothing shows up as "unsupported", which looks the same as "exporter not installed". A filter that matches the wrong things looks healthy. For each source the tab answers four questions:

1. **What was selected?** The entities with their identity labels and how much data each had.
2. **Does it look like the project?** Sample label sets, names and titles.
3. **Which filter part removes the data?** For each filter part, the count with the full filter, the count without that part, and the values that exist for it.
4. **What was sent?** The scoped queries or requests, without secrets.

## Decisions (2026-10-20)

- **Snapshot in the report, not live.** Each source records `ScopeDiagnostics` during `collect`. It travels in `CollectionResult.diagnostics` and is stored as `AnalysisReport.diagnostics` (default `[]`, schema `2.8`). The tab therefore explains this report even if the data changes later. A live "Check filter" in the project editor, reusing the same code, is a later follow-up.
- **Near-miss checks run on every analysis**, not only when a source found nothing. They are bounded, read-only and run after collection, so they cannot change any series. If a check fails or times out, it becomes a note (`diagnostic_failed`) on the snapshot. It never makes the source fail and never makes the job `partial`.
- **Diagnostics never feed detection.** No `DETECTOR_VERSION` bump, no change to `config_hash`, no AI input. They are not part of `_fit` budget trimming beyond their own bounds.
- **All five source kinds in one task.** Prometheus, Cloudflare, Sentry, Wazuh and Grafana. Each fills the same generic contract, so the UI has one renderer.
- **Name:** the "Scope" tab, route `/reports/:id/scope`.

## Contract (`app/domain/diagnostics.py`)

```text
ScopeDiagnostics
  kind: SourceKind
  summary: str                      # e.g. "3 jobs, 12 instances, 418 series"
  selection: list[SelectedEntity]   # ≤ 50, sorted by count desc
  selection_total: int              # entities before the cap
  samples: list[Labels]             # ≤ 5 full label sets / identifying fields
  checks: list[FilterCheck]         # one per filter part (≤ 160)
  requests: list[str]               # ≤ 120, deduplicated, no secrets (Prometheus lists every planned signal query)
  notes: list[Reason]               # e.g. group_empty, project_not_found, diagnostic_failed

SelectedEntity
  display_name: str
  labels: Labels                    # identity labels
  count: int                        # series (Prometheus) or events/alerts/instances
  unit: str                         # "series" | "requests" | "events" | "alerts" | "instances"
  families: list[SignalFamily]

FilterCheck
  part: str                         # rendered, e.g. env=~"prod|staging", environment=production
  field: str                        # "matcher" | "zone" | "hostname" | "project" | "environment" | "tag"
                                    # | "agent" | "group" | "label" | "folder" | "rule_uid"
                                    # | "rule_label" | "instance_label"
  matched: int | None               # with the full filter; null if unknown
  matched_without: int | None       # with this part dropped; null if not applicable / failed
  unit: str
  available: list[str]              # ≤ 20 values that exist for this field within the rest of the filter
  available_truncated: bool
  matching: list[str]               # the subset of `available` this part accepts
```

A listed item (folder, rule UID, Sentry project, Wazuh agent or group, Cloudflare hostname) widens the selection rather than narrowing it: its `matched` is what that item selects within the rest of the filter, and `matched_without` is null.

The UI verdicts, in order:
- **Excludes all data**: `matched == 0` and `matched_without > 0`;
- **No such value**: values exist but none is accepted, and the list is not truncated;
- **Matches nothing**: `matched == 0`;
- **No effect**: `matched == matched_without`;
- otherwise OK.

## Per source

- **Prometheus** (`sources/prometheus/source.py`, `synthetic.py`):
  - Selection comes from the collected series: unique entity keys, series per entity, and families. Samples are the labels of up to 5 series. Requests are the discovery query plus each distinct `MetricSeries.query`, including custom rules.
  - Checks are one per scope matcher, all evaluated at the frozen `T` over the catalog metric union used by discovery (`{__name__=~"<required>"}`):
    - `matched` = `count({__name__=~…, <all matchers>})`;
    - `matched_without` = the same with this matcher dropped;
    - `available` = `label_values(<matcher label>, match[]={__name__=~…, <other matchers>}, limit=21)`, which uses the existing uncalled `PrometheusClient.label_values`;
    - `matching` = `available` filtered by `LabelMatcher.matches`.
  - Dropping a matcher can leave a selector without a restricting matcher, for example in a single-matcher project. These queries are therefore a separate, narrow path: `promql.leave_one_out(scope, dropped)` with its own assertion that every other scope matcher is present. They are only ever `count(...)` and label values over the catalog metric union, never user PromQL or range queries. Record this exception in `docs/DECISIONS.md` and in the `AGENTS.md` invariants next to the custom-rule rule.
  - `SyntheticMetricsSource` produces equivalent diagnostics from its scenario data.
- **Cloudflare** (`sources/cloudflare/`):
  - Selection is the zone (ID → resolved name) and each configured hostname with its 24 h requests.
  - Checks:
    - zone: zone found, and requests in 24 h;
    - each hostname: requests with the hostname filter vs. without it, and `available` from a new `CloudflareApi.top_hosts(start, end, limit)` (GraphQL group by `clientRequestHTTPHost`).
  - A synthetic counterpart is required.
- **Sentry** (`sources/sentry/`):
  - Selection is the resolved projects (`infos()`) with their 24 h error and transaction counts (`last_day`).
  - Checks:
    - project: a slug that is configured but doesn't resolve is reported as `project_not_found`;
    - environment: `available` from a new `SentryApi.environments(project)` (`/projects/{org}/{project}/environments/`), merged across projects; `matched_without` = 24 h errors without the environment filter;
    - each tag: 24 h errors with vs. without it, via `series`.
  - A synthetic counterpart is required.
- **Wazuh** (`sources/wazuh/`):
  - Selection is the agents from `discover()` with alert counts and their groups. Silent expected agents are listed with count 0.
  - Checks:
    - each group: member count from `members()`, where an empty group is reported as `group_empty`;
    - each label: matching agents with vs. without it, using `agents(None, …)` with that label removed (the labels become a per-call argument in `client.py`);
    - each agent name: whether it exists and has alerts.
  - Reuse `_selection_exclusions` for the notes.
- **Grafana** (`sources/grafana/`): everything is already in memory from `api.rules()`, so no new API calls are needed.
  - Selection is the matched rules (title, folder, UID) with active instances kept.
  - Checks:
    - each folder, UID and rule label: rules matched with vs. without it; `available` = existing folders, or the existing values of that rule-label key;
    - each instance label: instances kept with vs. without it.
  - The notes reuse `unmatched`, `truncated`, `crowded` and `unlabelled`.

Merging: `service._gather` concatenates `diagnostics` like `totals`. A source that failed has no snapshot, and the tab shows its `source_unavailable` exclusion instead.

## UI

- A "Scope" tab in `ReportLayout.vue` (`router.ts` child `scope`) with one panel per source, in the order of `report.sources`.
  - The header shows the kind, `base_url` and the summary.
  - The filter-checks table shows part, matched, matched without, and the available values as chips, with the matching ones highlighted. It flags **excludes everything** in the critical colour and **no effect** muted.
  - The selection table uses `DataTable`, with "showing 50 of N" when capped.
  - Samples and notes follow.
  - Requests are collapsible, with a copy button per query.
- States:
  - reports older than 2.8: an empty state, "This report predates scope diagnostics; run a new analysis";
  - a failed source: its exclusion;
  - loading, keyboard tab navigation and narrow screens as for the other tabs.

## Acceptance

- [x] `ScopeDiagnostics` contract, `CollectionResult.diagnostics`, `AnalysisReport.diagnostics` and schema 2.8. 2.7 reports still load with `diagnostics == []`. `make contracts` and `make fixtures` are run and drift-clean.
- [x] Prometheus:
  - leave-one-out counts and label values for every matcher, through `leave_one_out` with its own assertion;
  - no unscoped query other than those `count`/label-values forms (covered by a test on the rendered queries);
  - a wrong matcher (e.g. `env="prod"` where only `production` exists) yields `matched == 0`, `matched_without > 0`, and `production` in `available`.
- [x] Cloudflare, Sentry, Wazuh and Grafana each produce selection, checks and notes on their synthetic scenarios. The new Api methods have synthetic counterparts and client tests.
- [x] A failing or timing-out diagnostic becomes a `diagnostic_failed` note. The job state, the series and the findings are unchanged (test with a failing check).
- [x] Bounds are enforced: 50 entities, 5 samples, 20 values, 30 requests. No secrets appear in requests (test with credentialed sources).
- [x] The UI Scope tab covers the panels, flags, empty, old-report and failed-source states, plus vitest coverage.
- [x] Docs: `docs/contracts.md`, `docs/UI_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` (the leave-one-out exception) and `AGENTS.md`. `make check` passes. Do a demo run (`make demo`) with a deliberately wrong filter and check the tab.

## Deviations from the plan

- **Sentry `project_not_found` cannot occur.** `SentryApi.projects()` already fails the whole source (AUTH) when a slug does not resolve, so the source shows its failure instead.
- **Sentry tag checks have no `available` values.** The tag-values API was not added.
- **Wazuh group checks have no `available` values.** There is no API to list groups.
- **Cloudflare.** Its new API method is `host_requests(start, end, hostnames, limit)`, used twice: once for the zone's busiest hostnames, once for the configured hostnames.
- **Synthetic sources.**
  - Synthetic Prometheus, Sentry and Wazuh data carry no out-of-scope data, environments or labels, so their filters change nothing (`matched == matched_without`). Sentry still lists `production` and `staging`.
  - Synthetic Cloudflare has fixed hostnames, so a mistyped hostname has no requests.
  - Grafana's synthetic rules are filtered for real.

## Remaining limitations / follow-ups

- Live "Check filter" in the project editor, to iterate on a filter before saving, using the same diagnostics code.
- Near-miss checks drop one filter part at a time. Combinations of two wrong parts show only as "excludes everything" on both.
- Not yet run against live VictoriaMetrics, Cloudflare, Sentry, Wazuh or Grafana:
  - the Prometheus label-values `limit` parameter needs Prometheus ≥ 2.51 or VictoriaMetrics;
  - the Sentry environments endpoint and the Cloudflare `count_DESC` grouping follow the documented APIs.
- A leave-one-out count on a single-matcher project counts the catalog metrics across the whole TSDB at one instant. That is cheap, but not free, on a very large VictoriaMetrics.

## Completion record

- Completed date: 2026-10-20
- Changed files:
  - New backend files:
    - `backend/app/domain/diagnostics.py`
    - `backend/app/sources/{prometheus,cloudflare,sentry,wazuh,grafana}/diagnostics.py`
    - `backend/tests/test_diagnostics.py`
  - New frontend files:
    - `frontend/src/lib/diagnostics.ts` and its test
    - `frontend/src/views/report/ScopeView.vue`
  - Changed:
    - Domain: `common` (2.8), `interfaces`, `report`.
    - `service.py`.
    - Prometheus: `promql` (`assert_leave_one_out`), `source`, `synthetic`.
    - Each other source kind's `api`, `client`, `source` and `synthetic`; the Sentry and Cloudflare `catalog`.
    - Source tests' fakes.
    - Frontend: router, `ReportLayout` (Scope tab with problem count), `AppRail`, API types, `App.test.ts`.
    - Regenerated contracts, `schema.d.ts` and fixtures.
  - Docs: `contracts.md`, `UI_SPEC.md` (§8a), `ARCHITECTURE.md`, `DECISIONS.md` (the leave-one-out exception), `AGENTS.md`.
- Checks:
  - `make contracts`, `make fixtures` and `make check` pass: backend 1333 passed, 6 skipped; frontend vitest, vue-tsc, eslint and build all pass.
  - Live run of the native app with `DEMO_PROJECTS=true AI_PROVIDER=fake` (not the Docker `make demo`).
  - Test project: a copy of the incident project with a wrong Grafana folder `Shopp`, Sentry environment `prod`, Wazuh group `shop-webb` and an extra Cloudflare hostname. Its schema 2.8 report flagged:
    - the folder as "No such value" (available `Platform`, `Shop`);
    - the environment as "No such value" (available `production`, `staging`);
    - the group as "Matches nothing".

    The Scope tab showed all five sources (screenshot checked).
  - No secrets in requests: the request texts are built from catalog documents and paths, not from client headers. They are covered by a Sentry client test that checks the token is absent, not by a test for every credentialed source.
