# T024 — Fast report loading

Dependencies: T023. Status: see [task index](README.md).

## Outcome

Opening a report downloads what its views show, not every finding's chart data. A report of a large project (424 findings, 9.2 MB raw, 1.9 MB gzipped) took about 4.5 s to download over a remote link before anything rendered; tab switches and rendering were already fast (20–340 ms). The download was the cost: evidence (4.6 MB) and related metrics (2.9 MB) made up most of it, at full float precision.

## Changes

- **Split API.** `GET /api/analyses/{id}/report/summary` returns `ReportSummary`: the snapshot without `evidence` and `related_metrics`. `GET /api/analyses/{id}/report/findings/{finding_id}` returns `FindingDetail`: the finding's evidence (in `evidence_ids` order) and its related metrics (`FindingRelatedMetrics`: policy, evaluation, comparisons by rank, the charts they reference; null before schema 2.9). Unknown findings are `404 finding_not_found`. `GET /api/analyses/{id}/report` still returns the full snapshot. The stored snapshot is unchanged; the API slices it (`AnalysisReport.summary()`, `.finding_detail()`).
- **Parsed-report cache.** The repository keeps the last 4 parsed snapshots (immutable, frozen models) and parses off the event loop, so a report's summary and finding requests parse it once.
- **Chart precision.** Persisted chart values (evidence and related-chart series, bands, deviations) keep 5 significant digits (`app/analysis/charts.py`, `chart_value`). Finding numbers, detection and `DETECTOR_VERSION` are unchanged. Older reports keep their stored precision.
- **UI.** The report layout loads the summary. The Overview's six finding cards fetch their own details (placeholder of the sparkline's size meanwhile); an opened finding fetches its detail (chart-sized placeholder; on failure a message with Retry, and Related metrics stays hidden). Details are cached for the session (`staleTime: Infinity`).

## Acceptance

- [x] Opening a report requests the summary and the details of the Overview's cards only, never the full report.
- [x] Opening a finding requests that finding's detail; its evidence chart, data table, query inspector and related metrics render from it.
- [x] Reports before schema 2.9 still open; their findings say related metrics are not available.
- [x] A failing detail request is shown with Retry and never as missing evidence.
- [x] Chart arrays keep 5 significant digits; finding numbers are unchanged.
- [x] `make check` passes.

## Verification

Measured on the 424-finding report above with the new code: summary 126 KB gzipped (was 1.9 MB for the full report), finding detail median 4.7 KB (max 21.7 KB), Overview (summary + 6 cards) 183 KB. On synthetic reports, rounding chart arrays to 5 digits cut the gzipped full report by about 55 %.

## Completion record

- Backend: `app/domain/report.py` (`ReportSummary`, `AnalysisReport.summary()/finding_detail()`, `FindingDetail`, `FindingRelatedMetrics`), `app/domain/jobs.py` (`finding_not_found`), `app/api/routes.py`, `app/storage/repository.py` (cache, parse in thread), `app/analysis/charts.py`, `engine.py`, `correlation.py` (`chart_value`). Tests: `tests/test_api.py`, `tests/test_analysis.py`.
- Frontend: `api/queries.ts` (`useReport` → summary, `useFindingDetail(s)`), `views/report/*` (context without evidence, `EvidencePanel` owns the detail query, `RelatedMetricsPanel` takes it as a prop, `OverviewView` fetches its cards), `components/FindingCard.vue` (placeholder), `lib/related.ts` (`relatedFor(detail)`), summary type in libs. Tests: `App.test.ts`, `lib/related.test.ts`, helper `test-report.ts`.
- Generated: `docs/contracts/*`, `frontend/src/api/schema.d.ts`, `fixtures/reports/*` (rounded chart values).
- Docs: `DECISIONS.md` §3, `contracts.md`, `ARCHITECTURE.md`, `UI_SPEC.md` §10, `AGENTS.md`.

## Remaining limitations / follow-ups

- The findings list still renders every row; with hundreds of findings an opened finding takes about 300 ms to render (12k DOM nodes). Virtualising the list would fix it.
- The ECharts chunk loads only once a chart is first shown; preloading it would save about 0.6 s on a direct link to a finding.
