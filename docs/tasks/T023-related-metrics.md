# T023 — Related metrics for incident investigation

Dependencies: T022. Status: see [task index](README.md).

## Outcome

Opening a finding shows a **Related metrics** panel with a few ranked comparisons that help answer: “What else changed around this problem?” Each comparison includes aligned charts, its timing, data quality, and the evidence for associating the resources.

Proposal recorded on 2026-10-22 at the owner's request for future implementation. The scope and numerical choices below are proposed defaults; thresholds and budgets need validation during implementation. This document does not start implementation.

## Existing foundation

- `backend/app/analysis/engine.py` already populates `Finding.related_finding_ids` using overlapping or nearby episodes and resource relationships, such as the same entity, service, database server, storage cluster, or a verified shared host. The configured timing slack is currently 30 minutes.
- `frontend/src/lib/findings.ts` and `frontend/src/components/IncidentTimeline.vue` show episodes that coincide in time. This does not measure similarity between their metric values.
- Collected metrics share the analysis time grid, currently five minutes. Derived series and per-step baselines are available during detection.
- Saved evidence currently contains finding charts around each episode, with six hours of padding on either side. Arbitrary metrics without findings are not generally retained in reports.

The new feature should preserve the meaning of existing finding relationships and add explicit evidence for metric comparisons.

## Investigation experience

An illustrative example, not a result observed in this repository:

| Metric | Observed change begins |
| --- | --- |
| PostgreSQL connection usage | 14:05 UTC |
| API latency | 14:10 UTC |
| Sentry errors | 14:15 UTC |

Opening the latency finding could show: “Connection usage and latency show similar changes. Connection usage leads by approximately five minutes. The database-to-service association is unverified.”

The panel presents three independent kinds of evidence:

| Evidence | Meaning |
| --- | --- |
| Resource association | The metrics concern the same resource or resources connected by a verified mapping. |
| Similar changes | Their values or deviations from baseline move together, in the same or opposite directions. |
| Timing | Episodes overlap, an event occurs nearby, or a supported comparison suggests a delay between changes. |

Correlation and temporal order support investigation hypotheses. They do not establish causation. A shared project alone does not establish a service, host, or dependency relationship.

## Proposed first version

1. Start from each latest-day finding and compare a bounded set of relevant series already collected for that project, including custom-rule signals. A candidate does not need its own finding: a gradual resource change may still help explain an incident.
2. Prefer candidates with verified resource associations. Permit comparisons across source kinds within the project, while labelling an unknown resource association explicitly. Keep resource association separate from statistical strength.
3. Evaluate a documented window around the episode, including surrounding observations where available. Use the same comparison window for competing candidates, disclose clipping and coverage, and avoid choosing a window solely because it maximizes a score.
4. Rank useful comparisons deterministically and show a small number by default. Record why each was selected. The candidate limit, total work budget, ranking policy and display limit must be explicit and validated against representative project sizes.
5. Save the results and sufficient chart data with the report so reopening it requires no source queries.

The initial feature uses existing collected data. Arbitrary metric discovery, additional source queries, a resource-mapping editor, automatic incident grouping, causal graphs, and automated remediation are follow-ups.

## Numerical policy and safeguards

- **Compare unusual changes.** Use deviations from the existing preceding-data baseline where it is adequate. This can reduce ordinary time-of-day effects. Normalizing values alone does not remove shared trends or dependence between successive samples.
- **Choose and validate the statistic.** Evaluate Pearson correlation on baseline deviations and rank correlation as implementation candidates. Record the selected method and preprocessing in the numerical policy. Avoid presenting a coefficient as a probability that the metrics are related.
- **Keep direction.** Positive and negative relationships can both be useful, such as free memory falling while latency rises. Preserve the sign instead of showing only an absolute strength.
- **Bound lag exploration.** Compare a small, documented range of delays in multiples of the analysis step. Distinguish an observed onset difference from an estimated lag. Five-minute buckets cannot support second-level ordering; broad or ambiguous peaks should be labelled inconclusive.
- **Account for searching many pairs and lags.** A maximum coefficient can occur by chance. Use bounded candidate selection and synthetic negative controls to tune ranking. Do not claim statistical significance without a method that accounts for time dependence and multiple comparisons.
- **Preserve gaps.** Align timestamps and compare only jointly observed samples, including after a lag shift. Never replace missing values with zero or join disconnected observations into a false continuous sequence. Report paired sample count, paired coverage and substantial gaps.
- **Require enough information.** Constant series, very few paired observations, too little variation, inadequate baseline or short episodes can make a coefficient unavailable. Store a reason. A resource association or timing observation can still be shown with its own evidence type.
- **Treat events separately.** OOMs, restarts and alert firings often need event timing or interval overlap rather than continuous-series correlation. Shared zero buckets alone are not useful evidence of a relationship.
- **Recognize shared inputs.** Ratios, their operands, duplicate series, and alerts generated from the same metric can agree mechanically. Identify known dependencies and avoid ranking them as independent confirmation. Do not assume arbitrary custom PromQL dependencies are fully understood.
- **Preserve existing conclusions.** Correlation must not increase a finding's severity or data-quality confidence. Missing sources, insufficient data and truncation remain visible; no retained comparison does not mean no relationship exists.
- **Keep scope and identity.** Use only the project's collected series. T022 scope diagnostics never feed correlation. Matching display names or arbitrary label values across source namespaces does not establish identity.

Background: [Esri's explanation of time-series cross correlation](https://doc.esri.com/en/arcgis-pro/latest/tool-reference/space-time-pattern-mining/how-time-series-cross-correlation-works.html) describes how trends, seasonality, autocorrelation and searching multiple lags affect interpretation. The numerical policy must be validated for this app's data and five-minute resolution.

## Architecture and ownership

The implementing task owns the domain contracts, deterministic analysis, report persistence integration, finding-detail UI, fixtures and documentation below.

- Add a focused analysis module, proposed as `backend/app/analysis/correlation.py`. Invoke it after per-series detection while derived arrays and baseline evaluations are still available, before report evidence is trimmed and before optional AI explanation. Keep the growing `engine.py` orchestration small.
- Add contracts in a dedicated domain module, proposed as `backend/app/domain/correlation.py`, and carry the result through `DetectionResult` into `AnalysisReport`. Keep `related_finding_ids` as existing resource-based finding links.
- A comparison needs stable identity; its anchor finding; both series' source, signal and entity identities; an optional candidate finding reference; resource-association evidence; method and preprocessing; comparison window and step; signed coefficient and lag when available; paired sample count and coverage; quality/availability reasons; and saved chart references.
- A candidate may have no finding. Introduce suitable chart storage or generalize evidence references without fabricating a finding to satisfy the current `Evidence.finding_id` requirement. Store shared series once where practical, with raw units, relevant expected values and query provenance.
- Record per-finding evaluation status so the UI distinguishes evaluated with no retained matches, insufficient data, skipped by a budget, and unavailable in an older report. Bound both computation and saved evidence. Budget trimming must keep all references valid and disclose omitted comparisons.
- Bump the report schema and preserve older snapshots. Bump `DETECTOR_VERSION` for the new deterministic analysis behavior; include configurable numerical policy in `config_hash` so results are reproducible.
- AI may explain retained comparisons through bounded, validated references. Numerical computation and ranking remain independent of AI. If the AI contract is extended, validate comparison references and preserve the existing behavior when explanation fails.

Update `docs/DECISIONS.md`, `docs/detection.md`, `docs/contracts.md`, `docs/ARCHITECTURE.md`, `docs/UI_SPEC.md` and `AGENTS.md` when the feature is implemented. Regenerate contracts and fixtures through the existing commands.

## UI

- Put the **Related metrics** panel inside the expanded finding detail. Each row identifies the metric, entity and source, explains its selection, and shows resource association, direction, approximate timing and quality limitations.
- Selecting a row shows two charts with a shared UTC time axis and synchronized inspection. Preserve separate units and visible gaps. An optional baseline-deviation view must label its scale clearly.
- Keep charts on their actual timestamps by default. If a lag-adjusted view is offered, explicitly show the shift. Display the analysed window, paired coverage and approximate resolution alongside the evidence.
- Link to a candidate's finding when one exists; otherwise make its stored comparison chart inspectable directly. Provide a data-table alternative and keyboard access.
- Cover loading, no retained matches, insufficient data, partial sources, budget omissions, older reports and narrow screens. Avoid wording that identifies a root cause or claims that no comparison means healthy.

## Implementation sequence

1. Settle and document the numerical method, window, lag sign convention, eligibility rules and budgets using synthetic positive and negative examples. Freeze the domain contracts before building the UI.
2. Implement deterministic candidate selection and evaluation, including event timing and known shared-input handling. Integrate with detection and versioning.
3. Persist comparisons and their chart evidence; enforce reference validation, report size bounds and compatibility with older reports. Extend AI input only through validated contracts if included.
4. Build the Related metrics panel and synchronized charts. Regenerate artifacts, update docs, validate the synthetic demo and run the full gate.

## Acceptance

- [ ] A finding shows ranked comparisons with resource, similarity and timing evidence kept distinct.
- [ ] A useful candidate without its own finding can be inspected from a saved report.
- [ ] Positive, inverse and lagged synthetic relationships are found with the documented sign convention; ambiguous timing is not presented as precise ordering.
- [ ] Unrelated series and shared daily cycles do not automatically rank as strong incident evidence. Tests include multiple candidate pairs and lags.
- [ ] Sparse events, constant series, insufficient history, gaps and weak overlap produce appropriate evidence or explicit unavailable reasons.
- [ ] Known duplicate/derived signals and alerts based on the same metric are not counted as independent confirmation.
- [ ] Cross-source comparisons preserve source identities and label unknown resource associations; no name-based host or service attribution is introduced.
- [ ] The existing finding severities, confidence, coverage and source-failure semantics are preserved. AI-disabled and AI-failed runs retain identical numerical comparisons.
- [ ] Identical inputs and configuration yield identical results and ordering. Compute and storage caps are exercised, omissions are disclosed, and all retained references resolve.
- [ ] Older reports load; a new report reopened from SQLite reproduces comparisons and charts without contacting a source.
- [ ] The UI covers accessibility, narrow screens, no matches, unavailable comparisons, old reports and partial results.
- [ ] Contracts and fixtures are regenerated; required documentation is updated; `make check` passes and the synthetic investigation flow is visually verified.

## Verification

Use deterministic synthetic series for simultaneous and delayed changes, inverse movement, ordinary daily patterns, unrelated autocorrelated series, sparse events, gaps, flat values, short history and derived operands. Include a useful metric below its own anomaly threshold. Keep numerical tests independent of AI and live services.

Exercise persistence with real SQLite and old report fixtures, reference integrity after budget trimming, and frontend behavior with saved synthetic reports. Record actual check commands and results. Live-source tuning can supplement these checks but must be distinguished from synthetic evidence.

## Completion record

To be filled by the implementing agent with changed files, actual checks and results, deviations, and remaining limitations.
