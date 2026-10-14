# T020 — Custom rules: user PromQL analysed with the catalog

Dependencies: T019. Status: see [task index](README.md).

## Outcome

Users can add their own rules to a project, for example an extra finding from a business metric that only they know (failed checkouts, a mail queue, consumer counts). The first version supports Prometheus only. A rule is a PromQL query plus how to judge it: against its own baseline, against a fixed threshold, both, or as events. Each rule becomes one catalog signal and one analysis rule, so collection budgets, baselines, 14-day trends, severity, confidence, recurrence, evidence and AI explanations all apply unchanged.

## Decisions (2026-10-14)

- **Per project, stored with the Prometheus source** (`PrometheusSourceInput.custom_rules`, at most 20). The source config is JSON, so no migration is needed. A shared rule library is left for later.
- **Family `custom` only** (entity kind `custom`). Users do not pick built-in families, so custom rules never mix into catalog coverage. `custom` is not in `SOURCE_FAMILIES[prometheus]`, so a failed source reports no custom row for projects without rules.
- **Scope injection, not scope templates.** Users write plain PromQL. A lexer (`app/domain/user_promql.py`) inserts the project's matchers into every selector and the result goes through the existing `assert_scoped`. The following are rejected:
  - `@` and negative `offset`, because they leak future data into baselines;
  - `or` inside matchers, because MetricsQL alternatives would bypass the scope;
  - top-level comparisons without `bool`, because filtered-out series would look healthy, so the rule's threshold is used instead;
  - unknown `$` placeholders;
  - selector-free queries.

  Prometheus' `/api/v1/parse_query` was not used: it is experimental, and VictoriaMetrics does not have it.
- **Modes:**
  - `anomaly`: the baseline, with a direction and a minimum change factor (default 1.5) and/or a minimum absolute change;
  - `threshold`: at or above, or at or below, held for N minutes, with an optional critical level and no baseline;
  - `both`;
  - `event`: any value ≥ 0.5 per step, with the rule's severity (default high).

  `max_severity` caps the other modes. "At or below" thresholds are new in the detector (`Rule.absolute_below`), and so are threshold-only rules (`Rule.relative = False`). Neither lowers confidence for missing baselines.
- **Rules travel with the data.** The source returns its rules in `CollectionResult.custom_rules`. The detector builds analysis rules from them, the finding-ID key includes a hash of them (an edited rule starts new IDs), and the report keeps a copy (`AnalysisReport.custom_rules`, schema 2.5). Rules that change between submit and run cannot mismatch.
- **Thresholds are the user's.** Titles read "(threshold)" and evidence reads "custom rule threshold", never "heuristic/not an SLO". The rule's description goes to the AI input as untrusted text (`FindingDigest.rule_description`).
- **Preview:** `POST /api/projects/custom-rules/preview` runs one draft rule over the last 24 h, reusing stored secrets via `project_id`. Query errors are returned in the body (`ok: false`).
- `DETECTOR_VERSION` was bumped to `detectors-2026.10.14`. Large numbers are now formatted whole (2000, not 2e+03) in the backend and the UI.

## Acceptance

- [x] The lexer injects the scope into every selector form (bare metric, metric with matchers, bare matchers, binary operands, subqueries, recording-rule names, `by`/`on`/`group_left` label lists left alone, strings with braces), and the rendered queries pass `assert_scoped`. Unsafe or malformed queries are rejected with a reason.
- [x] Rule validation covers: slug, mode/threshold consistency, critical beyond threshold in the threshold's direction, identity labels, and duplicate slugs per project.
- [x] Capability: missing metrics or a failing query make a rule `unsupported` with the reason. Collected series are scoped, out-of-scope series are dropped and disclosed, and entities use identity labels (or all labels minus the scope).
- [x] Detection on `synthetic://incident` gives one finding per mode, with correct titles, thresholds, severities and below-threshold peaks. `healthy` gives none. Threshold-only rules on `short-history` are evaluated. An edited rule changes finding IDs, and series without their rule are not analysed.
- [x] API: rules round-trip through create/update, unsafe rules give 422, and preview works on synthetic sources and needs a restricting matcher.
- [x] UI: rules editor with automatic identifier, client validation, server errors mapped to the rule's fields, and a preview with query, series, latest value and sparkline. The project page lists rules. Finding details show the rule's description, what it does, and "set in the custom rule".
- [x] Demo projects carry two example rules. `make contracts`, `make fixtures` and `make check` pass.

## Remaining limitations / follow-ups

- Not yet run against a live VictoriaMetrics/Prometheus. The lexer covers PromQL and common MetricsQL. MetricsQL-only syntax such as `WITH` templates, or metric names containing `.`, may be rejected or fail at the capability step with a reason (never run unscoped).
- Import of existing Prometheus/vmalert rule files (`expr > N` split into query plus threshold, `for:` into minutes) is not implemented.
- Rules are per project. There is no shared library or template.
- Validation messages from the API keep pydantic's "Value error, " prefix, as for every other field.

## Completion record

- Completed date: 2026-10-14
- Changed files:
  - New backend files:
    - `backend/app/domain/custom_rules.py`
    - `backend/app/domain/user_promql.py`
    - `backend/app/sources/prometheus/custom.py`
    - `backend/app/analysis/rules/custom.py`
    - `backend/tests/test_custom_rules.py`
  - New fixtures: `fixtures/reports/report_custom_rules.json`, `fixtures/api/custom_rule_preview.json`.
  - New frontend files:
    - `frontend/src/lib/customRules.ts`
    - `frontend/src/components/projects/CustomRulesEditor.vue`
    - `frontend/src/components/projects/ValueSparkline.vue`
  - Changed: domain (`common`, `projects`, `report`, `interfaces`, `explanation`, `detector_config`), Prometheus source and synthetic source, factory, storage, analysis (`rules/base`, `detect`, `derive`, `engine`), AI prompt and providers, service, bootstrap (demo rules), projects API, fixture and schema scripts, frontend project form, project page, evidence panel, format, API client and queries, and `App.test.ts`.
  - Docs: `metrics-catalog.md`, `detection.md`, `DECISIONS.md` (§3 route, §5), `contracts.md`, `UI_SPEC.md`, `ARCHITECTURE.md`, `AGENTS.md`.
- Checks:
  - `make contracts`, `make fixtures`, `make check-backend` (1105 passed, 5 skipped) and `make check-frontend` pass.
  - Live demo run (`DEMO_PROJECTS=true AI_PROVIDER=fake`, synthetic only): the incident analysis gave a schema 2.5 report with a critical "E-mail queue depth at or above 2000 (threshold)" finding and a high "Checkout failures above expected range" finding.
  - In the same live run, the preview endpoint returned the scoped query, and the project editor showed the preview in the browser.
