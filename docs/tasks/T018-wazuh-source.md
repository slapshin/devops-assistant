# T018 — Wazuh source: host security alerts

Dependencies: T017. Status: see [task index](README.md).

## Outcome

A project can include one Wazuh source that selects a set of Wazuh agents. Each agent is analysed as its own entity. Analyses cover its security alerts: all alerts, high-level alerts, authentication failures and file-integrity (FIM) changes. The same deterministic detector produces findings and trends from this data, so a brute-force wave, a burst of file changes or a jump in high-level rule hits on one host is reported next to the project's metrics, edge and application findings.

## Decisions (2026-10-06; implemented 2026-10-07)

These follow the T015/T017 pattern. Items marked **(owner)** were implemented with the stated default and are still open for confirmation (see "Open questions").

- **Data comes from the Wazuh indexer, not the Wazuh server API.** Alert history is only stored in the indexer (OpenSearch fork, default port 9200, daily indices `wazuh-alerts-4.x-YYYY.MM.DD`). The server API (port 55000) reports the current agent state and the manager's own logs, but it has no alert time series. One source needs one indexer URL, user and password. Read access to `wazuh-alerts-*` is enough (the docs should include a minimal role). The server API is out of scope for v1.
- **Queries are only `POST /<index_pattern>/_search` with `size: 0` and aggregations.** The query is built as JSON DSL with `term`/`terms`/`range` clauses and never as `query_string`, so a configured value cannot change the query (same guarantee as Sentry's quoted tags). The default index pattern is `wazuh-alerts-4.x-*` and can be changed (e.g. for a 5.x index name or a custom template).
- **Agent selection:** the source holds agent names (0–50), agent groups (0–10) and `agent.labels.<key> = value` filters (0–10, ANDed). At least one is required, so a project never silently analyses a whole fleet. The named agents and the groups' members together form the selection, and label filters narrow it (labels alone select every agent with those labels). Labels are set in the agent's `<labels>` block or a group's shared `agent.conf`.
- **Groups (owner request, 2026-10-07: "add ability add agents by group"):** alert documents do not carry an agent's groups. The owner chose the `wazuh-monitoring-*` index (agent snapshots with `name` and `group` that the Wazuh dashboard writes every 15 minutes when `wazuh.monitoring.enabled` is on) over the Wazuh server API, so no second URL or credentials are needed. A group's members are the agents in it in any snapshot of the 24 h before T, up to 1,000 per group (more: `series_truncated`). A group without members is disclosed (`no_data`) and never widens the selection to every agent. The monitoring index pattern is configurable (default `wazuh-monitoring-*`).
- **Entities:** one per agent (`agent.name`). New `EntityKind.AGENT` ("a Wazuh agent"). Agent names are not matched to Prometheus `instance` or node names, because the project forbids invented host relationships. The manager's own alerts (`agent.id = "000"`) are a separate entity.
- **Cardinality:** discovery returns at most 50 agents (terms aggregation with `size = cap + 1`). Any agents past the cap make the report `partial` with an exclusion, never a silent cut. A source is only useful for a bounded set of hosts.
- **Families:** new `HOST_SECURITY` ("host_security": HIDS alerts, auth failures) and `FILE_INTEGRITY` ("file_integrity": syscheck/FIM changes). They are kept separate from Cloudflare's `security` family so the UI's source filter and family labels stay unambiguous. `SOURCE_FAMILIES[WAZUH] = (HOST_SECURITY, FILE_INTEGRITY)`.
- **Signals** (5-minute counts, analysed per second like Sentry, zero-filled inside fetched chunks):
  | Signal | Filter | Rule | Family |
  | --- | --- | --- | --- |
  | `wazuh_alerts` | all alerts (indexer stores level ≥ 3 by default) | `hids_alert_rate` | host_security |
  | `wazuh_high_alerts` | `rule.level >= 12` (Wazuh's "high" band) | `hids_high_alert_rate` | host_security |
  | `wazuh_auth_failures` | `rule.groups` ∈ {`authentication_failed`, `authentication_failures`, `invalid_login`} | `hids_auth_failure_rate` | host_security |
  | `wazuh_fim_changes` | `rule.groups = syscheck` | `fim_change_rate` | file_integrity |
- **Rules:** all are `LEVEL` and `Dir.UP`, capped at `HIGH` like the Cloudflare security rules (FIM at `MEDIUM`, since routine updates change files too): an alert surge is a signal to investigate, not an outage. `hids_high_alert_rate` uses a low `abs_floor`, so a few level-12+ hits on a normally quiet agent count. An `EVENT` rule ("any level-15 alert") was considered and deferred, because level-15 noise varies a lot between rulesets **(owner)**.
- **Out of scope for v1:** agent connectivity (the `wazuh-monitoring-*` index has 15-minute snapshots made by the dashboard plugin, which is optional), vulnerability detection (`wazuh-states-vulnerabilities-*` is a current-state index, not a time series), SCA scores, MITRE breakdowns, the Wazuh server API and archives indices.

## Work

### 1. Domain (`app/domain/`)

- `common.py`: `SourceKind.WAZUH`, `SignalFamily.HOST_SECURITY` and `FILE_INTEGRITY` with docstrings, a `SOURCE_FAMILIES` entry, and `EntityKind.AGENT`.
- `projects.py`:
  - `WazuhLabel {key, value}` validates `key` against `[A-Za-z0-9_.-]+` and normalises like `SentryTag`.
  - `WazuhSourceInput { kind, api_url, index_pattern = "wazuh-alerts-4.x-*", agents (0–50 names), labels (0–10), username, password (write-only, kept when omitted on edit), tls_verify = true }` (`tls_verify` matches the other kinds). A validator requires `agents` or `labels`. `index_pattern` allows no `,`, whitespace or leading `-` (no index exclusion tricks), and `synthetic://` is allowed for `api_url`.
  - `WazuhSource` read model (`password_set`), `WazuhConnection`, and members of `SourceInput` / `AnySource` / `SourceConnection`.
- `ConnectionTest` description: add a Wazuh sentence.
- `detector_config.py`: thresholds `hids_alert_rate`, `hids_high_alert_rate`, `hids_auth_failure_rate`, `fim_change_rate`. Bump `DETECTOR_VERSION` (→ `detectors-2026.10.9`).

### 2. Source package `app/sources/wazuh/` (mirrors `sentry/`)

- `api.py`: the `WazuhApi` protocol: `begin(end_time)`, `agents(start, end) -> AgentList` (names, IDs and a `truncated` flag), `series(agents, start, end) -> Chunk` (signal → agent → bucket → count), `info() -> ClusterInfo` (distribution and version), `aclose()`.
- `catalog.py`: `CATALOG_VERSION`, `WazuhSignal` (signal, family, unit, filter), and builders for the search body:
  - scope filter: `range timestamp [start, end)` (`format: epoch_second`), `terms agent.name`, and `term agent.labels.<key>` per label;
  - discovery: `terms agent.name size=cap+1`;
  - collection: `terms agent.name` (the discovered set) → `date_histogram timestamp fixed_interval=5m, min_doc_count=0, extended_bounds` → `filters` sub-aggregation, one bucket per signal.
  - Pick the chunk length so `agents × buckets × signals` stays under OpenSearch's default `search.max_buckets` (65 535). The budget is 40,000 buckets: 16-hour chunks at 50 agents, seven days for up to 4 agents. It is computed, not hard-coded.
- `client.py`: a bounded httpx client (timeout, concurrency 4, response size cap, one retry on 429/5xx with `Retry-After`), basic auth, `verify_tls`, and the URL path prefix kept (reverse proxies). Error mapping: 401/403 → AUTH; `index_not_found_exception` or `_shards.total == 0` → a clear "index pattern matches no indices" error; `too_many_buckets_exception` → a bug-class error (the chunk math is wrong); 3xx → reported with its target. The password never appears in errors or logs. A version check accepts `opensearch`/`wazuh` distributions and warns on Elasticsearch, whose aggregation response is compatible but untested.
- `source.py`: capabilities = discovery over the full window (one request) and families that have any data. Collection runs per chunk; a failed chunk makes those buckets unknown and adds an exclusion. Each agent's history starts at its first alert within the 28-day window, so new agents get `short-history` instead of fake zeros. Unknown is never treated as healthy.
- `synthetic.py`: `SyntheticWazuhApi` with `healthy`, `incident` (an SSH brute-force wave on one agent plus a FIM burst on another), `short-history` and `degraded` (failing chunks).
- `connect.py`: `connect_wazuh(conn)` (synthetic or real) and `probe_wazuh(source, scope)` (capabilities over the window, then a 24 h discovery for the alert count). A test with zero alerts is a note, not a warning, because a quiet host is legitimate.

### 3. Detection (`app/analysis/`)

- `rules/host_security.py` (`HOST_SECURITY_RULES`, including the FIM rule) registered in `rules/__init__.py`.
- `derive.py`: add the four `wazuh_*` → rule mappings to the analysed-as-collected table. There is no traffic spec, because alerts have no natural denominator.
- `docs/DECISIONS.md` §5 and `docs/detection.md`: thresholds and why they are relative-only and capped.

### 4. Wiring and API

- `sources/factory.py`: a `WazuhConnection` case in `open_source`.
- `storage/projects.py`: (de)serialise the config and encrypt the password. No migration is needed (one row per kind).
- `api/projects.py`: synthetic scenario validation, and test-connection and health dispatch for the new kind.
- `bootstrap.py`: demo projects get a `synthetic://<scenario>` Wazuh source.
- AI prompt (`app/ai/prompt.py`): check that the new families and entity kind read well in the evidence. Alert text such as `rule.description` is **not** sent; only counts are, as for the other sources.

### 5. Frontend

- `lib/projects.ts`: defaults and form mapping. `lib/format.ts`: family and entity labels ("Host security", "File integrity", "Agent").
- `ProjectFormView.vue`: a Wazuh fieldset (indexer URL, index pattern, agents as a comma/space-separated list, label filters as key/value rows like the Sentry tags, user, password with "keep stored", verify TLS, **Test Wazuh connection**).
- `ProjectView.vue`, `ProjectsView.vue`: a source row and health. `SourceIcon.vue`: a Wazuh icon (a generic shield glyph, no vendor logo).
- `App.test.ts`: create, edit (password kept), the test-connection result, and findings filtered by the Wazuh source.

### 6. Contracts, fixtures and docs

- `make contracts`, `make fixtures` (add `reports/report_wazuh.json` from the real pipeline over `synthetic://incident`, and `api/connection_test_wazuh.json`).
- Update `ARCHITECTURE.md`, `OPERATIONS.md` (indexer read-only role example, self-signed certs, Docker networking to port 9200), `UI_SPEC.md`, `metrics-catalog.md`, `AGENTS.md` (replace "Wazuh is planned" and add the package line) and the task index.

## Live verification (when an indexer and credentials are available)

- Field names and types on the owner's version: `timestamp` (date), `agent.name` (keyword), `agent.labels.*`, `rule.level` (long) and `rule.groups` (keyword). Check for Wazuh 4.x; for 5.x, check the index name and field layout first.
- That a read-only role limited to `wazuh-alerts-*` (plus `wazuh-monitoring-*` with groups) works for every request.
- That `wazuh-monitoring-*` maps `name` and `group` as keywords on the owner's version, and how long its indices are kept.
- Real `search.max_buckets` and response sizes for 50 agents × 16 h chunks. Also the index retention (ISM) relative to the 28-day window, which would otherwise surface as short history.
- Whether a reverse-proxied indexer (path prefix) and self-signed TLS work as configured.

## Acceptance

- [x] A Wazuh source can be created, edited (password kept when omitted), cloned and tested. The password is encrypted at rest and never returned or logged. A source with neither agents nor labels is rejected.
- [x] Against a mocked transport, the client sends the documented DSL (no `query_string`, values only in `term`/`terms`), honours the path prefix and TLS setting, chunks within the bucket budget, and maps auth, missing-index, redirect, rate-limit and server errors without echoing the password.
- [x] Discovery past 50 agents makes the report `partial` with an exclusion. Failed chunks are unknown with exclusions. New agents get short history, not zeros.
- [x] `synthetic://incident` yields auth-failure, high-alert and FIM findings on the expected agents; `healthy` yields none; `degraded` is `partial`.
- [x] A Wazuh failure in a mixed project makes the report `partial`, not `failed`.
- [x] The UI covers loading, error, empty-test and narrow-screen states for the Wazuh fieldset and source row.
- [x] `make contracts`, `make fixtures` and `make check` pass.

## Open questions for the owner

1. Which Wazuh version runs, and is the indexer reachable from where the app runs (port 9200, TLS, proxy)?
2. ~~Agent selection~~: answered — by names, groups (via the monitoring index) and/or labels.
3. Should agent connectivity (disconnected agents) be in v1? It needs `wazuh-monitoring-*` or the server API.

## Completion record

- Completed date: 2026-10-07
- Actual changed files and artifacts:
  - new: `backend/app/sources/wazuh/` (`api.py`, `catalog.py`, `client.py`, `source.py`, `synthetic.py`, `connect.py`), `app/analysis/rules/host_security.py`, `tests/test_wazuh.py`, fixtures `reports/report_wazuh.json` (generated by the real pipeline) and `api/connection_test_wazuh.json`
  - changed: `domain/common.py` (kind, two families, entity kind `agent`), `domain/projects.py` (Wazuh models), `domain/detector_config.py`, `analysis/derive.py`, `analysis/rules/__init__.py`, `storage/projects.py`, `sources/factory.py`, `api/projects.py`, `bootstrap.py` (demo projects get a label-selected Wazuh source), `scripts/generate_fixtures.py`; frontend `lib/projects.ts`, `lib/format.ts`, `ProjectFormView.vue`, `ProjectView.vue`, `ProjectsView.vue`, `SourceIcon.vue`, `App.test.ts`; regenerated contracts, `schema.d.ts` and fixtures (detector version bump)
  - docs: `DECISIONS.md` §5, `detection.md`, `metrics-catalog.md`, `contracts.md` (new non-partial exclusion code `no_data`), `OPERATIONS.md`, `UI_SPEC.md`, `ARCHITECTURE.md`, `AGENTS.md`
- Commands/checks and results: `make contracts`, `make fixtures`, `make check` pass (backend 892 passed / 4 skipped, frontend 46 passed).
- Follow-up (2026-10-07, groups): `groups` and `monitoring_index_pattern` on the source; `catalog.members_body`, `WazuhApi.group_members`, `WazuhMetricsSource.selection` (named agents ∪ group members), group lines in the connection test message, an *Agent groups* field in the form; demo projects select their agents by group. Tests in `tests/test_wazuh.py` and `App.test.ts`.
- Remaining limitations: built without a live Wazuh indexer (see "Live verification"); field names follow the Wazuh 4.x alerts template, and 5.x is untested. `agent.labels.*` filtering assumes the template maps label values as keywords. Thresholds are first guesses from synthetic data and need tuning on real alert volumes. No agent connectivity, vulnerability or SCA signals; no Wazuh server API. Group membership depends on dashboard monitoring being enabled and lags by up to 15 minutes.
