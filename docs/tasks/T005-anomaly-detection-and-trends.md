# T005 — Anomaly detection and two-week trends

Dependencies: T002. Status: see [task index](README.md).

## Outcome

Deterministic, evidence-backed anomaly episodes and comparable daily trends are computed independently of AI and the UI.

## Ownership

`backend/app/analysis/`, detector configuration, and analysis tests.

## Work

- Implement pure numerical detectors and the [baseline/coverage policy](../ARCHITECTURE.md#time-windows-and-baseline-policy), without LLM dependencies.
- Cover resource saturation/pressure, capacity decline, I/O/network anomalies, supported container events, traffic shifts, HTTP 5xx/4xx, RPC failures, and supported latency percentiles.
- Combine documented configurable absolute heuristics with robust relative deviations, effect-size floors, minimum traffic/sample requirements, and persistence.
- Merge adjacent anomalous samples into episodes; define gap handling, boundary clipping, stable IDs, and recurrence matching across days.
- Distinguish ongoing, resolved, new, and recurring findings. Summarize trend direction with supporting measures and confidence; label sparse evidence inconclusive.
- Group related symptoms only using verified identity mappings or explicitly labeled time correlation. Preserve individual evidence.
- Record query evidence, observed/expected values, units, times, and coverage for each finding. Define deterministic ordering and severity rules.

## Acceptance

- [x] Supported resource/request detectors implement documented absolute/relative checks, persistence, and minimum signal requirements.
- [x] Baseline construction follows the architecture and never uses the evaluated day or future observations.
- [x] Coverage, baseline length, zero MAD, low volume, and missing values affect availability/confidence explicitly.
- [x] Episode grouping, boundary clipping, severity, recurrence, and trend calculations are deterministic and versioned.
- [x] Trend normalization handles changing resource populations and observation gaps.
- [x] Cross-layer attribution requires verified mappings; timing-only correlation remains labeled as a hypothesis.

## Verification

Exercise sustained CPU load, memory growth, disk depletion, independent 404 and 5xx bursts, latency shifts, traffic drops, low-volume routes, gaps, zero-variance baselines, and short retention. Verify no future leakage, stable episode boundaries, and trends when hosts appear/disappear using controlled synthetic series.

## Completion record

- Completed date: 2026-09-30
- Actual changed files and artifacts:
  - `backend/app/analysis/{rules,derive,baseline,detect,engine}.py`
  - `backend/app/metrics/synthetic.py` (deterministic scenario source)
  - `backend/app/domain/{findings,report,interfaces,detector_config}.py`: `FindingState`, `Recurrence`, `TrendSummary`, and thresholds for all signals
  - `backend/tests/test_analysis.py`
  - regenerated fixtures and schemas
  - `docs/detection.md`, plus updates to `docs/DECISIONS.md` §5 and `docs/contracts.md`
  - correction of the T003 filesystem note
- Commands/checks and results:
  - `uv run pytest` passes (227 passed, 2 skipped); ruff and mypy strict are clean.
  - The analysis suite has 53 tests and runs in 13 s. It covers every verification scenario, including future-leakage tampering, zero MAD, low volume, gaps, short retention, and a changing population.
  - Live snapshot (paas/production, 360 series): detection took 4.3 s after optimisation (15.8 s before) and produced 1 medium and 8 low findings; the trend is stable. See `docs/detection.md`.
- Decisions or dependency changes:
  - The 4xx ratio was replaced by 404 and other-4xx rates.
  - Throughput severity caps; fixed event points.
  - Relations require verified identity or mapping plus time overlap.
  - IDs exclude `analysis_id`.
  - `SyntheticMetricsSource` is available for T007–T010 offline use; it reports `backend="synthetic"`.
  - No new dependencies.
- Remaining limitations or blockers:
  - The thresholds are provisional; disk-throughput bursts dominate the live episodes (capped at low).
  - Mappings are an instant snapshot at T.
  - Time-of-day bands with 14 days do not model weekly patterns.
- Next ready task: T006.
