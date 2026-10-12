# T019 — Grafana alerts source: alert rule state history

Dependencies: T018. Status: see [task index](README.md).

## Outcome

A project can include one Grafana source that selects a set of Grafana-managed alert rules and alert instances. The analysis reads the rules' state history for the latest 24 h and the 14 preceding days and turns it into series. The deterministic detector then reports firing alerts as findings next to the project's metrics, edge, application and host-security findings. A finding says when a rule fired, how many instances fired, and whether that rule fires every day (`recurrence`). The AI explanation can then link an alert to the anomalies around it.

## Live observations (owner's Grafana, 2026-10-12, read-only)

- Grafana **12.4.5**. About 95 Grafana-managed rules in folders such as Infrastructure, PaaS, Payments, Crypto, Sales alertings, Fullstack and Piper. No data-source-managed rules were checked.
- Rule labels are sparse: `type` (`infrastructure`, `sites`, `paas`, `fullstack`) and sometimes `env`. No rule has a `severity` label. The **project** is only on alert *instance* labels from the query result (`project=pw-app`, `project=pron`, `exported_project`, `server`, `provider`, `env=production`).
- State history uses the **annotations backend**: `GET /api/annotations?type=alert&from=&to=&limit=` returns transitions (`alertId`, `alertName`, `prevState`, `newState`, `time`, `text`, `data.values`). History goes back to at least 2026-09-21, so a 14-day baseline is available. Volume is about 1,350 transitions/day across all rules (≈20k per 15-day window); `limit=5000` is accepted.
- Instance labels exist **only inside `text`**: `"<title> {alertname=…, grafana_folder=…, project=pw-app, …} - A=1.0, B=1.0"`. Values can contain `, `, `{`, `}` and newlines (a ClickHouse `exception` label carries a full stack trace). `/api/v1/rules/history?ruleUID=` returns the same text in a data frame and ignores `limit` (it returned 100 rows), so it is no better.
- States seen: `Normal`, `Normal (MissingSeries|NoData|Paused|Updated)`, `Pending`, `Pending (NoData)`, `Alerting`, `NoData`. Current state and structured instance labels are available from `GET /api/prometheus/grafana/api/v1/rules`.
- No Loki datasource is configured, so the Loki state-history backend (which keeps structured labels) is not in use.

## Decisions (2026-10-12; implemented 2026-10-12)

The owner chose path A ("read data by grafana service account", 2026-10-12). Other **(owner)** items were implemented with the stated default and are still open for confirmation.

Implementation refinements from the live API (2026-10-12): `GET /api/annotations` accepts `alertUID`, so history is fetched **per selected rule** (no join on `alertId`). Annotation tags turned out to be lossy (`:` in values becomes `_`, e.g. `instance:https_//…`; long values and empty values are dropped), so the **text is authoritative** and tags only fill in labels when the text has no label set. Over a 3.5-day sample of 4,129 real alert annotations the text parser read every label set (0 unparsed). History reached back about 28 days.

- **Alerts become series, not a new report section (owner).** Each selected rule is an entity (`EntityKind.ALERT_RULE`, "a Grafana alert rule"). Its state history becomes series on the 5-minute grid, and the existing detector, evidence, timeline, trends, recurrence and AI pipeline handle it unchanged. A separate "alerts" list in the report was considered and deferred: it needs a report schema bump, a new UI section and new AI input, and most of its value comes from the timeline and findings anyway.
- **Data path A — Grafana HTTP API (default, works today).** Needs one Grafana URL and a service-account token with the Viewer role (`alert.rules:read`, `annotations:read`).
  - Rules: `GET /api/prometheus/grafana/api/v1/rules` gives uid, title, folder, group, rule labels, health and current instances with structured labels and `activeAt`.
  - History: `GET /api/annotations?type=alert&alertUID=<uid>&from=&to=&limit=5000` per selected rule, paged backwards by `to` (dedupe by annotation `id`), with a cap of 50k changes per rule. Past the cap that rule's older states are unknown (`series_truncated`).
  - Instance labels are parsed from `text`: strip the `<title> ` prefix and the trailing `} - <values>`, then split before `, name=`. A change without a label set cannot match instance labels and is disclosed (`no_data`), never silently matched. Tags only fill in labels the text lacks (they are lossy).
- **Data path B — Prometheus state-history backend (not chosen).** Grafana can write alert state as a `GRAFANA_ALERTS` series to a Prometheus-compatible datasource (VictoriaMetrics here) through `[unified_alerting.state_history]`. A new `alerts` module in the Prometheus catalog would then read `GRAFANA_ALERTS{alertstate="firing", <project matchers>}`. Labels stay structured and the existing project matchers scope it, with no new source kind. Costs: a Grafana config change (check feature-flag status on 12.4), and history only starts after the switch (14 days until a full baseline). If the owner prefers B, this task shrinks to a catalog module, a rule module and docs.
- **Selection:** folders (0–20, by UID or title), rule UIDs (0–100), rule label matchers (0–10, equality, ANDed) and **instance label matchers** (0–10, equality, ANDed, e.g. `project=pw-app`, `env=production`). At least one of folders, rule UIDs or rule labels is required, so a project never silently analyses every rule. Instance matchers then narrow which instances count. With equality-only matchers and no regex, no configured value can widen the scope. The cap is 100 rules per source; more makes the report `partial`.
- **Signals** (per rule, 5-minute buckets, reconstructed from transitions; the state at the window start comes from the first transition's `prevState`, or from `activeAt` for instances with no transition in the window):
  | Signal | Meaning | Rule | Family |
  | --- | --- | --- | --- |
  | `grafana_alert_firing` | instances in `Alerting`/`Recovering` at any time in the bucket (rules without a known `severity`) | `alert_firing` (medium) | alerting |
  | `grafana_alert_firing_critical` | the same, rules with `severity` = critical/high/page/p1/emergency | `alert_firing_critical` (high) | alerting |
  | `grafana_alert_firing_info` | the same, rules with `severity` = info/informational/low/none/p4/p5 | `alert_firing_info` (low) | alerting |
  | `grafana_alert_nodata` | instances in `NoData`/`Error` at any time in the bucket (rule cannot evaluate) | `alert_nodata` (low) | alerting |
  `Pending` and the `Normal (*)` variants count as not firing. `Paused` intervals are gaps (`None`), not zeros, so a paused rule is never reported as healthy.
- **Rules:** all four are `EVENT` rules (any step with an instance firing is anomalous; no baseline, so event-only trend buckets need none). Severity comes from the rule's `severity` label through the signal it is collected as (`critical` → high, `info` → low, anything else or none → MEDIUM **(owner)**); it does not grow with duration or instance count (the instance count is the evidence value). Existing recurrence marks rules that fire every day as `recurring`, so chronic alerts do not look new. `alert_nodata` is capped at LOW and its confidence notes "rule could not evaluate". A relative "fires more than usual" rule (instances or transitions vs. the 14-day baseline) is deferred.
- **Family:** new `SignalFamily.ALERTING` ("alerting": rules defined by the team in Grafana). `SOURCE_FAMILIES[GRAFANA] = (ALERTING,)`.
- **Privacy:** the AI prompt gets the rule title, folder and the instance labels used for selection, not annotation text, `summary`/`description` annotations or evaluated values **(owner)**. This matches Wazuh, where `rule.description` is not sent. The UI may show the summary from the rules API.
- **Out of scope for v1:** data-source-managed (Prometheus/Loki ruler) rules, Alertmanager silences, contact points and notification history, Grafana OnCall/Incident, and writing anything to Grafana.

## Work

### 1. Domain (`app/domain/`)

- `common.py`: `SourceKind.GRAFANA`, `SignalFamily.ALERTING`, `SOURCE_FAMILIES` entry, `EntityKind.ALERT_RULE`.
- `projects.py`: `GrafanaMatcher {name, value}` (label-name validation like `WazuhLabel`); `GrafanaSourceInput { kind, api_url, org_id = 1, folders, rule_uids, rule_labels, instance_labels, token (write-only, kept when omitted), tls_verify = true }` with a validator requiring folders, rule UIDs or rule labels; `synthetic://` allowed. Also the `GrafanaSource` read model (`token_set`), `GrafanaConnection`, and union members.
- `detector_config.py`: `alert_firing` and `alert_nodata` thresholds; bump `DETECTOR_VERSION`.

### 2. Source package `app/sources/grafana/` (mirrors `wazuh/`)

- `api.py`: `GrafanaApi` protocol: `rules() -> RuleList`, `transitions(start, end) -> TransitionPage` (paged), `info()` (`/api/health`: version), `aclose()`.
- `catalog.py`: `CATALOG_VERSION`, `GrafanaSignal`, state classification (`Alerting` / `NoData|Error` / paused / other), the label-text parser, and rule/instance matching.
- `client.py`: bounded httpx client (timeout, concurrency 4, response size cap, one retry on 429/5xx), bearer token, `X-Grafana-Org-Id`, path prefix kept, `verify_tls`. Errors: 401/403 → AUTH (message names the needed permissions), 404 on the rules API → "unified alerting disabled", 3xx with target. The token never appears in errors or logs.
- `source.py`: capabilities = rules matched plus whether history exists in the window. Collection: rules → selected rule ids → paged transitions over `[T-15d, T)` → per-instance state intervals → per-rule bucket series (gaps for paused rules and before a rule's creation/first transition, so new rules get short history). Exclusions for capped rules, capped transitions and unparsed labels.
- `synthetic.py`: `SyntheticGrafanaApi` with `healthy` (no firing), `incident` (one rule firing for 2 h with 3 instances, plus a chronic daily-firing rule), `short-history` and `degraded` (failing history pages). Synthetic text should include a multi-line label value to exercise the parser.
- `connect.py`: `connect_grafana(conn)` and `probe_grafana(source, scope)`: the version, how many rules match, how many are firing now, and the oldest history seen (a warning if under 14 days).

### 3. Detection (`app/analysis/`)

- `rules/alerting.py` (`ALERTING_RULES`) registered in `rules/__init__.py`; `derive.py` mappings for the two signals; finding attributes: folder, group and the top 5 firing instances' selection labels.
- `docs/DECISIONS.md` §5 and `docs/detection.md`: why the rules are absolute, the severity mapping and how recurrence applies.

### 4. Wiring and API

- `sources/factory.py`, `storage/projects.py` (encrypted token), `api/projects.py` (scenario validation, test-connection, health), `bootstrap.py` (demo projects get `synthetic://<scenario>`), and a check of `app/ai/prompt.py` against the privacy decision.

### 5. Frontend

- `lib/projects.ts`, `lib/format.ts` ("Alerting", "Alert rule"). `ProjectFormView.vue` gets a Grafana fieldset: URL, org ID, folders, rule UIDs, rule label and instance label rows, token with "keep stored", verify TLS and **Test Grafana connection**. `ProjectView.vue` / `ProjectsView.vue` get a source row and health; `SourceIcon.vue` gets a generic bell glyph. `App.test.ts` covers create/edit/test and the source filter.
- Nice to have: the finding detail links to the rule in Grafana (`<api_url>/alerting/grafana/<uid>/view`).

### 6. Contracts, fixtures and docs

- `make contracts`, `make fixtures` (`reports/report_grafana.json` from `synthetic://incident`, `api/connection_test_grafana.json`); `ARCHITECTURE.md`, `OPERATIONS.md` (service-account setup, Viewer role, Docker networking), `UI_SPEC.md`, `metrics-catalog.md`, `AGENTS.md`, task index.

## Live verification

- With a Viewer-role service account: both endpoints, paging over 15 days, and response sizes.
- Parser accuracy on the real history: the share of transitions whose `project`/`env` parse cleanly (the `exception` label from ClickHouse rules is the known hard case).
- `alertId` ↔ rule id mapping on 12.4, and behaviour for deleted or renamed rules (history without a current rule → an attribute "rule deleted", not dropped).
- Annotation retention (`[annotations.alerting] max_age`) against the 15-day window.
- Example scope: a `pw-app` project with folders Infrastructure + Fullstack and instance matcher `project=pw-app` gives sensible findings for 2026-10-11/12.

## Acceptance

- [x] A Grafana source can be created, edited (token kept), cloned and tested; the token is encrypted at rest and never returned or logged; a source with no rule selector is rejected.
- [x] Against a mocked transport: paging, dedupe, caps, org header, path prefix, TLS setting and error mapping, with no token in errors.
- [x] The parser handles multi-line and comma-containing values without matching them wrongly; unparsed instances are disclosed.
- [x] State reconstruction: firing that started before the window, paused rules as gaps, unretained history as unknown.
- [x] `synthetic://incident` yields an `alert_firing` finding on the expected rule and a `recurring` one on the chronic rule; `healthy` yields none; `degraded` is `partial`.
- [x] A Grafana failure in a mixed project makes the report `partial`, not `failed`.
- [x] `make contracts`, `make fixtures` and `make check` pass.

## Open questions for the owner

1. ~~Path A or B~~: answered — path A, Grafana service account (2026-10-12).
2. Firing alerts as findings only (implemented), or also a separate alert list in the report?
3. Default severity for rules without a `severity` label is MEDIUM (implemented); none of the owner's rules has one yet, so adding `severity` labels in Grafana makes findings more precise.
4. The AI prompt gets rule titles, folder/group and selected instance labels, never annotation text or `summary` (implemented); should `summary` be allowed?
5. Is the Grafana reachable from where the app runs (Docker networking), with a Viewer service-account token?

## Completion record

- Completed date: 2026-10-12
- Actual changed files and artifacts:
  - new: `backend/app/sources/grafana/` (`api.py`, `catalog.py`, `client.py`, `source.py`, `synthetic.py`, `connect.py`), `app/analysis/rules/alerting.py`, `tests/test_grafana.py`, fixtures `reports/report_grafana.json` (real pipeline over `synthetic://incident`) and `api/connection_test_grafana.json`
  - changed: `domain/common.py` (kind `grafana`, family `alerting`, entity kind `alert_rule`), `domain/projects.py` (Grafana models), `domain/metrics.py` (`ATTRIBUTE_LABELS`), `domain/detector_config.py` (→ `detectors-2026.10.12`), `analysis/derive.py` (signal mapping, attribute labels), `analysis/engine.py` (event-only trend buckets need no baseline), `analysis/rules/__init__.py`, `storage/projects.py`, `sources/factory.py`, `api/projects.py`, `bootstrap.py` (demo projects get a Grafana source: folder Shop, `project=shop`), `scripts/generate_fixtures.py`; frontend `lib/projects.ts`, `lib/format.ts`, `ProjectFormView.vue`, `ProjectView.vue`, `ProjectsView.vue`, `SourceIcon.vue`, `App.test.ts`; regenerated contracts, `schema.d.ts` and fixtures
  - docs: `DECISIONS.md` §5, `detection.md`, `metrics-catalog.md`, `contracts.md`, `OPERATIONS.md`, `UI_SPEC.md`, `ARCHITECTURE.md`, `AGENTS.md`
- Commands/checks and results: `make contracts`, `make fixtures`, `make check` pass (backend 943 passed / 5 skipped, frontend 52 passed). Live read-only checks against the owner's Grafana 12.4.5 via the Grafana MCP (rules API shape, `alertUID` filter, annotation text/tags, parser over 4,129 annotations).
- Remaining limitations: the HTTP client itself has not run against the live Grafana with a service account token (only the same endpoints through the MCP). Only Grafana-managed rules and the annotations state-history backend are supported (Loki/Prometheus history backends and data-source-managed rules are not). A rule renamed within the window keeps working (identity is the UID), but its old annotations parse with the old title prefix. Instances that never changed state use the current state for the whole window, so a rule created during the window looks quiet before its creation. No relative "fires more than usual" rule yet.
