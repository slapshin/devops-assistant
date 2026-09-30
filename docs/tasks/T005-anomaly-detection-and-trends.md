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

- [ ] Supported resource/request detectors implement documented absolute/relative checks, persistence, and minimum signal requirements.
- [ ] Baseline construction follows the architecture and never uses the evaluated day or future observations.
- [ ] Coverage, baseline length, zero MAD, low volume, and missing values affect availability/confidence explicitly.
- [ ] Episode grouping, boundary clipping, severity, recurrence, and trend calculations are deterministic and versioned.
- [ ] Trend normalization handles changing resource populations and observation gaps.
- [ ] Cross-layer attribution requires verified mappings; timing-only correlation remains labeled as a hypothesis.

## Verification

Exercise sustained CPU load, memory growth, disk depletion, independent 404 and 5xx bursts, latency shifts, traffic drops, low-volume routes, gaps, zero-variance baselines, and short retention. Verify no future leakage, stable episode boundaries, and trends when hosts appear/disappear using controlled synthetic series.

## Completion record

Not started. Fill in after execution:

- Completed date:
- Actual changed files and artifacts:
- Commands/checks and results:
- Decisions or dependency changes:
- Remaining limitations or blockers:
- Next ready task:
