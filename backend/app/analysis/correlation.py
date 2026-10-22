"""Related metrics (T023): what else changed around each finding, deterministically.

For each latest-day finding (the anchor), other analysed series of the project (candidates)
are compared over one window around the anchor's episode:

- eligibility: the candidate has an unusual step (event, flagged step, or |robust z| at or
  above ``change_z``) during the episode, widened by the lag range and the overlap slack;
- known shared inputs (a ratio and its request count, 4xx and the 404s subtracted from it)
  are excluded, never counted as independent confirmation;
- level series with adequate baselines: Pearson correlation of their baseline deviations
  (robust z clipped to ±``deviation_clip``) over jointly observed steps, at shifts of up to
  ``max_lag_minutes`` in both directions. A lead or lag is claimed only when the shifts
  within ``lag_tolerance`` of the best all lie on one side of zero;
- event series and series without an adequate baseline: overlap of unusual intervals.

Resource association (identity labels and verified mappings, `relations.py`) is recorded
separately from the statistic, and orders the kept comparisons before their strength. Nothing
here changes findings. The policy is documented in docs/DECISIONS.md §5.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray

from app.analysis.charts import chart_value, slice_series
from app.analysis.detect import TREND_STEPS, Episode, SeriesEvaluation
from app.analysis.relations import HostMappings, association, component_association, hosts_for
from app.analysis.rules import RuleKind
from app.domain.common import SOURCE_FAMILIES, Reason, SignalFamily, SourceKind, TimeRange
from app.domain.correlation import (
    ComparisonMethod,
    ComparisonTiming,
    RelatedChart,
    RelatedDirection,
    RelatedEvaluation,
    RelatedMetric,
    RelatedMetrics,
    RelatedPolicy,
    RelatedSeries,
    RelatedState,
    ResourceAssociation,
    SharedInput,
    UnavailableCount,
)
from app.domain.detector_config import DetectorConfig, RelatedConfig
from app.domain.findings import Finding
from app.domain.ids import comparison_id, related_chart_id
from app.domain.report import AnalysisWindows

Mask = NDArray[np.bool_]

METHOD_DESCRIPTION = (
    "Pearson correlation of baseline deviations (robust z, clipped) over jointly observed "
    "steps at bounded lags; overlap of unusual intervals for events and series without an "
    "adequate baseline."
)
REDUCED_PAIRED_COVERAGE = 0.8
GAP_NOTE_STEPS = 6
"""Unpaired runs from this length (30 min) are listed as gaps."""

_FAMILY_SOURCE = {f: kind for kind, fams in SOURCE_FAMILIES.items() for f in fams} | {
    SignalFamily.CUSTOM: SourceKind.PROMETHEUS
}
_OPAQUE_FAMILIES = {SignalFamily.ALERTING, SignalFamily.CUSTOM}
"""Alert rules and custom rules run queries this app does not analyse: their agreement with a
metric may be mechanical."""


@dataclass
class _Prepared:
    index: int
    ev: SeriesEvaluation
    deviation: NDArray[np.float64]
    """Clipped robust z per trend step; NaN without a value or an adequate baseline."""
    observed: Mask
    active: Mask
    """Unusual steps: events, flagged steps, or |z| >= change_z."""
    active_cum: NDArray[np.int64]
    source: SourceKind
    hosts: set[str]
    findings: list[tuple[Episode, str]] = field(default_factory=list)

    @property
    def is_level(self) -> bool:
        return self.ev.series.rule.kind is RuleKind.LEVEL

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.ev.series.rule.name, self.ev.series.entity.key)

    def active_in(self, lo: int, hi: int) -> int:
        lo, hi = max(0, lo), min(TREND_STEPS, hi)
        return int(self.active_cum[hi] - self.active_cum[lo]) if hi > lo else 0


@dataclass(frozen=True)
class _Window:
    lo: int
    hi: int
    episode: Episode
    reasons: tuple[Reason, ...]

    @property
    def steps(self) -> int:
        return self.hi - self.lo


@dataclass
class _Outcome:
    method: ComparisonMethod
    coefficient: float | None = None
    timing: ComparisonTiming = ComparisonTiming.INCONCLUSIVE
    lag_steps: int | None = None
    paired: int = 0
    longest_gap: int = 0
    overlap: int = 0
    strength: float = 0.0
    reasons: list[Reason] = field(default_factory=list)
    shared_input: SharedInput = SharedInput.NONE
    unavailable: str | None = None
    kept: bool = False


@dataclass
class _Run:
    """Accumulates comparisons and the charts they reference across findings."""

    key: str
    windows: AnalysisWindows
    config: DetectorConfig
    budget: int
    charts: dict[str, RelatedChart] = field(default_factory=dict)


def _prepare(
    evaluations: Sequence[SeriesEvaluation],
    finding_for_episode: dict[tuple[int, int], str],
    mappings: HostMappings,
    rc: RelatedConfig,
) -> list[_Prepared]:
    out = []
    for i, ev in enumerate(evaluations):
        with np.errstate(invalid="ignore"):
            deviation = np.clip(ev.z, -rc.deviation_clip, rc.deviation_clip)
            large = np.abs(ev.z) >= rc.change_z
        observed = ~np.isnan(ev.values)
        active = observed & ((ev.event_flag > 0) | (ev.abs_level > 0) | (ev.rel_flag > 0) | large)
        cum = np.concatenate([[0], np.cumsum(active, dtype=np.int64)])
        prepared = _Prepared(
            i,
            ev,
            deviation,
            observed,
            active,
            cum,
            _FAMILY_SOURCE[ev.series.rule.family],
            hosts_for(ev.series.entity, mappings),
        )
        prepared.findings = [
            (ep, finding_for_episode[(i, id(ep))])
            for ep in ev.episodes
            if (i, id(ep)) in finding_for_episode
        ]
        out.append(prepared)
    return out


def related_metrics(
    key: str,
    evaluations: Sequence[SeriesEvaluation],
    findings: Sequence[Finding],
    finding_for_episode: dict[tuple[int, int], str],
    mappings: HostMappings,
    windows: AnalysisWindows,
    config: DetectorConfig,
) -> RelatedMetrics:
    """Comparisons of every finding (most severe first) within the run's budget."""
    rc = config.related
    prepared = _prepare(evaluations, finding_for_episode, mappings, rc)
    anchors = {fid: (p, ep) for p in prepared for ep, fid in p.findings}
    run = _Run(key, windows, config, rc.max_evaluations)

    evaluations_out: list[RelatedEvaluation] = []
    comparisons: list[RelatedMetric] = []
    for f in findings:
        anchor, ep = anchors[f.finding_id]
        evaluation, kept = _evaluate_finding(run, f, anchor, ep, prepared)
        evaluations_out.append(evaluation)
        comparisons += kept

    referenced = {c.anchor.chart_id for c in comparisons} | {
        c.candidate.chart_id for c in comparisons
    }
    step = windows.step_seconds
    return RelatedMetrics(
        policy=RelatedPolicy(
            method=METHOD_DESCRIPTION,
            window_pad_seconds=rc.window_pad_minutes * 60,
            max_window_seconds=rc.max_window_minutes * 60,
            max_lag_seconds=_steps(rc.max_lag_minutes, step) * step,
            change_z=rc.change_z,
            min_coefficient=rc.min_coefficient,
            max_per_finding=rc.max_per_finding,
        ),
        evaluations=evaluations_out,
        comparisons=comparisons,
        charts=[run.charts[c] for c in sorted(referenced)],
    )


def _steps(minutes: int, step_seconds: int) -> int:
    return minutes * 60 // step_seconds


def _window(ep: Episode, rc: RelatedConfig, step_seconds: int) -> _Window:
    pad = _steps(rc.window_pad_minutes, step_seconds)
    longest = max(1, _steps(rc.max_window_minutes, step_seconds))
    lo, hi = max(0, ep.start - pad), min(TREND_STEPS, ep.end + pad)
    reasons = []
    if hi < ep.end + pad:
        reasons.append(
            Reason(
                code="window_clipped",
                message="The window ends at the analysis end: later observations do not exist yet.",
            )
        )
    if hi - lo > longest:
        lo = hi - longest
        reasons.append(
            Reason(
                code="window_truncated",
                message=f"The episode is longer than the {rc.max_window_minutes // 60} h "
                "window: only its latest part was compared.",
            )
        )
    elif lo > ep.start - pad:
        reasons.append(
            Reason(code="window_clipped", message="The window starts at the trend range start.")
        )
    return _Window(lo, hi, ep, tuple(reasons))


def _evaluate_finding(
    run: _Run, f: Finding, anchor: _Prepared, ep: Episode, prepared: Sequence[_Prepared]
) -> tuple[RelatedEvaluation, list[RelatedMetric]]:
    rc = run.config.related
    step = run.windows.step_seconds
    window = _window(ep, rc, step)
    lag = _steps(rc.max_lag_minutes, step)
    slack = _steps(run.config.relation_slack_minutes, step)
    span = TimeRange(start=_at(run, window.lo), end=_at(run, window.hi))
    reasons = list(window.reasons)

    def status(state: RelatedState, **counts: int) -> RelatedEvaluation:
        return RelatedEvaluation(
            finding_id=f.finding_id,
            state=state,
            window=span,
            candidates=counts.get("candidates", 0),
            evaluated=counts.get("evaluated", 0),
            retained=counts.get("retained", 0),
            omitted=counts.get("omitted", 0),
            not_evaluated=counts.get("not_evaluated", 0),
            shared_input=counts.get("shared_input", 0),
            unavailable=[
                UnavailableCount(code=code, count=n) for code, n in sorted(unavailable.items())
            ],
            reasons=reasons,
        )

    unavailable: dict[str, int] = defaultdict(int)
    if int(anchor.observed[window.lo : window.hi].sum()) < rc.min_paired_steps:
        reasons.append(
            Reason(
                code="anchor_sparse",
                message="Too few observations of this finding's series in the window to "
                "compare it with other series.",
            )
        )
        return status(RelatedState.INSUFFICIENT_DATA), []
    if anchor.is_level and _finite(anchor.deviation, window) < rc.min_paired_steps:
        reasons.append(
            Reason(
                code="anchor_baseline_unavailable",
                message="No adequate baseline for this finding's series: only unusual "
                "intervals were compared, not the shape of the changes.",
            )
        )

    shared = 0
    eligible: list[tuple[_Prepared, ResourceAssociation, str]] = []
    reach = lag + slack
    for p in prepared:
        if p.index == anchor.index or not p.active_in(ep.start - reach, ep.end + reach):
            continue
        if anchor.ev.series.inputs & p.ev.series.inputs:
            shared += 1
            continue
        a_entity, c_entity = anchor.ev.series.entity, p.ev.series.entity
        kind, basis = association(a_entity, c_entity, anchor.hosts, p.hosts)
        if kind is ResourceAssociation.UNKNOWN:
            kind, basis = component_association(a_entity, c_entity) or (kind, basis)
        eligible.append((p, kind, basis))
    if shared:
        reasons.append(
            Reason(
                code="shared_input",
                message=f"{shared} series computed from the same data (operands such as the "
                "request count) were not compared: they agree mechanically.",
            )
        )

    eligible.sort(
        key=lambda c: (
            c[1] is ResourceAssociation.UNKNOWN,
            -_peak_deviation(c[0], window),
            c[0].sort_key,
        )
    )
    take = min(len(eligible), rc.max_candidates_per_finding, run.budget)
    counts = {"candidates": len(eligible), "shared_input": shared}
    counts["not_evaluated"] = len(eligible) - take
    if eligible and take == 0:
        reasons.append(
            Reason(
                code="budget",
                message="The run's comparison budget was used by more severe findings.",
            )
        )
        return status(RelatedState.SKIPPED_BUDGET, **counts), []
    if counts["not_evaluated"]:
        reasons.append(
            Reason(
                code="candidates_capped",
                message=f"{counts['not_evaluated']} further eligible series were not compared "
                f"(at most {rc.max_candidates_per_finding} per finding, verified associations "
                "and the largest deviations first).",
            )
        )
    run.budget -= take

    kept: list[tuple[_Outcome, _Prepared, ResourceAssociation, str]] = []
    for p, kind, basis in eligible[:take]:
        outcome = _compare(anchor, p, window, lag, slack, rc)
        if outcome.unavailable:
            unavailable[outcome.unavailable] += 1
        elif outcome.kept:
            kept.append((outcome, p, kind, basis))

    def rank_key(k: tuple[_Outcome, _Prepared, ResourceAssociation, str]) -> tuple[object, ...]:
        return (
            k[2] is ResourceAssociation.UNKNOWN,
            k[0].shared_input is SharedInput.POSSIBLE,
            -round(k[0].strength, 6),
            k[1].sort_key,
        )

    kept.sort(key=rank_key)
    _mark_dependent([(o, p) for o, p, _, _ in kept])
    kept.sort(key=rank_key)
    retained = kept[: rc.max_per_finding]
    counts |= {
        "evaluated": take,
        "retained": len(retained),
        "omitted": len(kept) - len(retained),
    }
    out = [
        _comparison(run, f, anchor, window, rank, outcome, p, kind, basis)
        for rank, (outcome, p, kind, basis) in enumerate(retained, start=1)
    ]
    return status(RelatedState.EVALUATED, **counts), out


def _mark_dependent(ranked: list[tuple[_Outcome, _Prepared]]) -> None:
    """A candidate sharing data with a better-ranked one (a Sentry issue and its project's
    error total) does not confirm the finding independently of it."""
    for i, (outcome, cand) in enumerate(ranked):
        earlier = next(
            (p for _, p in ranked[:i] if p.ev.series.inputs & cand.ev.series.inputs), None
        )
        if earlier is not None and outcome.shared_input is SharedInput.NONE:
            outcome.shared_input = SharedInput.POSSIBLE
            outcome.reasons.append(
                Reason(
                    code="shares_candidate_input",
                    message=f"Computed from the same data as {earlier.ev.series.rule.label} "
                    f"on {earlier.ev.series.entity.display_name}: not independent of it.",
                )
            )


def _at(run: _Run, t: int) -> datetime:
    return run.windows.trend.start + timedelta(seconds=t * run.windows.step_seconds)


def _finite(values: NDArray[np.float64], window: _Window) -> int:
    return int(np.count_nonzero(~np.isnan(values[window.lo : window.hi])))


def _peak_deviation(p: _Prepared, window: _Window) -> float:
    """Largest |deviation| in the window (events: the clip), for candidate ordering."""
    part = p.deviation[window.lo : window.hi]
    if not p.is_level or np.all(np.isnan(part)):
        return float(np.count_nonzero(p.active[window.lo : window.hi]))
    return float(np.nanmax(np.abs(part)))


def _shifted(values: NDArray[np.float64], lo: int, hi: int, k: int) -> NDArray[np.float64]:
    """values[t - k] for t in [lo, hi); NaN outside the trend range."""
    out = np.full(hi - lo, np.nan)
    src_lo, src_hi = lo - k, hi - k
    a, b = max(0, src_lo), min(TREND_STEPS, src_hi)
    if b > a:
        out[a - src_lo : b - src_lo] = values[a:b]
    return out


def _longest_run(mask: Mask) -> int:
    padded = np.concatenate([[0], mask.astype(np.int8), [0]])
    edges = np.flatnonzero(np.diff(padded))
    return int(max((e - s for s, e in zip(edges[::2], edges[1::2], strict=True)), default=0))


def _dilate(mask: Mask, by: int) -> Mask:
    out = mask.copy()
    for k in range(1, by + 1):
        out[k:] |= mask[:-k]
        out[:-k] |= mask[k:]
    return out


def _compare(
    anchor: _Prepared, cand: _Prepared, window: _Window, lag: int, slack: int, rc: RelatedConfig
) -> _Outcome:
    lo, hi = window.lo, window.hi
    a_active = anchor.active[lo:hi]
    c_active = cand.active[lo:hi]
    paired_mask = anchor.observed[lo:hi] & cand.observed[lo:hi]
    overlap = int(np.count_nonzero(c_active & _dilate(a_active, slack)))

    correlate = (
        anchor.is_level
        and cand.is_level
        and _finite(anchor.deviation, window) >= rc.min_paired_steps
        and _finite(cand.deviation, window) >= rc.min_paired_steps
    )
    if correlate:
        outcome = _correlation(anchor, cand, window, lag, rc)
    else:
        outcome = _Outcome(
            ComparisonMethod.INTERVAL_OVERLAP,
            paired=int(np.count_nonzero(paired_mask)),
            longest_gap=_longest_run(~paired_mask),
        )
        if outcome.paired < rc.min_paired_steps:
            outcome.unavailable = "insufficient_pairs"
        elif overlap:
            # Share of the candidate's unusual steps that coincide with the finding's: a
            # chronic alert firing all window long is no evidence for this episode.
            outcome.strength = overlap / int(np.count_nonzero(c_active))
            outcome.kept = outcome.strength >= rc.min_overlap_share
            outcome.timing = ComparisonTiming.SIMULTANEOUS
        if cand.is_level and _finite(cand.deviation, window) < rc.min_paired_steps:
            outcome.reasons.append(
                Reason(
                    code="baseline_unavailable",
                    message="No adequate baseline for this series: only unusual intervals "
                    "were compared.",
                )
            )
    outcome.overlap = overlap
    if outcome.unavailable or not outcome.kept:
        return outcome

    coverage = outcome.paired / window.steps
    if coverage < REDUCED_PAIRED_COVERAGE:
        outcome.reasons.append(
            Reason(
                code="paired_coverage_reduced",
                message=f"Only {coverage:.0%} of the window's steps are observed in both series.",
            )
        )
    if outcome.longest_gap >= GAP_NOTE_STEPS:
        outcome.reasons.append(
            Reason(
                code="gap",
                message=f"A gap of {outcome.longest_gap} steps without paired observations; "
                "gaps are never filled.",
            )
        )
    _shared_input(anchor, cand, window, outcome, rc)
    return outcome


def _correlation(
    anchor: _Prepared, cand: _Prepared, window: _Window, lag: int, rc: RelatedConfig
) -> _Outcome:
    lo, hi = window.lo, window.hi
    a = anchor.deviation[lo:hi]
    coefficients: dict[int, tuple[float, int, int]] = {}
    constant: set[int] = set()
    for k in range(-lag, lag + 1):
        c = _shifted(cand.deviation, lo, hi, k)
        pair = ~np.isnan(a) & ~np.isnan(c)
        n = int(np.count_nonzero(pair))
        if n < rc.min_paired_steps or n / window.steps < rc.min_paired_coverage:
            continue
        x, y = a[pair], c[pair]
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            constant.add(k)
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        coefficients[k] = (r, n, _longest_run(~pair))

    outcome = _Outcome(ComparisonMethod.DEVIATION_CORRELATION)
    if not coefficients:
        outcome.unavailable = "constant" if constant else "insufficient_pairs"
        return outcome

    best = min(coefficients, key=lambda k: (-abs(coefficients[k][0]), abs(k), -k))
    floor = abs(coefficients[best][0]) - rc.lag_tolerance
    near = [k for k, (r, _, _) in coefficients.items() if abs(r) >= floor]
    chosen = best
    if 0 in near:
        chosen, outcome.timing = 0, ComparisonTiming.SIMULTANEOUS
    elif abs(best) == lag or not (all(k > 0 for k in near) or all(k < 0 for k in near)):
        # A best shift at the edge of the range says the changes do not overlap within it;
        # shifts on both sides agreeing as well say the order cannot be told apart.
        outcome.timing = ComparisonTiming.INCONCLUSIVE
        if 0 not in coefficients:
            outcome.unavailable = "constant" if 0 in constant else "insufficient_pairs"
            return outcome
        chosen = 0
    elif best > 0:
        outcome.timing = ComparisonTiming.CANDIDATE_LEADS
    else:
        outcome.timing = ComparisonTiming.CANDIDATE_LAGS
    r, n, gap = coefficients[chosen]
    outcome.coefficient = round(r, 4)
    outcome.lag_steps = chosen if outcome.timing in _LAGGED else None
    outcome.paired, outcome.longest_gap = n, gap
    outcome.strength = abs(r)
    outcome.kept = abs(r) >= rc.min_coefficient and _moves_in_episode(
        anchor, cand, window.episode, chosen, r, rc
    )
    return outcome


def _moves_in_episode(
    anchor: _Prepared, cand: _Prepared, ep: Episode, k: int, r: float, rc: RelatedConfig
) -> bool:
    """Whether the candidate itself deviates during the episode, in the direction the
    coefficient implies: agreement built only from the surrounding steps (one series high
    before, the other after) is no evidence about this episode."""
    a = anchor.deviation[ep.start : ep.end]
    c = _shifted(cand.deviation, ep.start, ep.end, k)
    if np.all(np.isnan(a)) or np.all(np.isnan(c)):
        return False
    a_mean, c_mean = float(np.nanmean(a)), float(np.nanmean(c))
    return abs(c_mean) >= rc.min_episode_deviation and np.sign(c_mean) == np.sign(a_mean * r)


_LAGGED = {ComparisonTiming.CANDIDATE_LEADS, ComparisonTiming.CANDIDATE_LAGS}


def _shared_input(
    anchor: _Prepared, cand: _Prepared, window: _Window, outcome: _Outcome, rc: RelatedConfig
) -> None:
    """Flag agreement that may be mechanical rather than independent confirmation."""
    families = {anchor.ev.series.rule.family, cand.ev.series.rule.family}
    prometheus_only = anchor.source is cand.source is SourceKind.PROMETHEUS
    if SignalFamily.ALERTING in families or (SignalFamily.CUSTOM in families and prometheus_only):
        outcome.shared_input = SharedInput.POSSIBLE
        outcome.reasons.append(
            Reason(
                code="possible_shared_input",
                message="An alert rule or custom rule may be computed from the same data: "
                "not independent confirmation.",
            )
        )
        return

    # Outside the window: series that only move together during an incident are not
    # duplicates, the same measurement agrees on ordinary days too. Series flat outside it
    # (counts that are usually zero) cannot tell.
    outside = np.ones(TREND_STEPS, dtype=bool)
    outside[window.lo : window.hi] = False
    a, c = anchor.ev.values[outside], cand.ev.values[outside]
    pair = ~np.isnan(a) & ~np.isnan(c)
    if np.count_nonzero(pair) >= rc.min_paired_steps and np.ptp(a[pair]) and np.ptp(c[pair]):
        raw = float(np.corrcoef(a[pair], c[pair])[0, 1])
        if abs(raw) >= rc.duplicate_coefficient:
            outcome.shared_input = SharedInput.POSSIBLE
            outcome.reasons.append(
                Reason(
                    code="near_identical",
                    message=f"The raw values agree almost exactly outside the window too "
                    f"(r = {raw:.2f}): "
                    "they may be the same measurement.",
                )
            )


def _onset(cand: _Prepared, window: _Window, lag: int, gap: int) -> int | None:
    """Start of the candidate's first unusual run that reaches the episode (± lag)."""
    ep = window.episode
    mask = cand.active[window.lo : window.hi]
    padded = np.concatenate([[0], mask.astype(np.int8), [0]])
    edges = np.flatnonzero(np.diff(padded))
    runs: list[list[int]] = []
    for s, e in zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True):
        if runs and s - runs[-1][1] <= gap:
            runs[-1][1] = e
        else:
            runs.append([s, e])
    for s, e in runs:
        start, end = window.lo + s, window.lo + e
        if start < ep.end + lag and end > ep.start - lag:
            return start
    return None


def _comparison(
    run: _Run,
    f: Finding,
    anchor: _Prepared,
    window: _Window,
    rank: int,
    outcome: _Outcome,
    cand: _Prepared,
    kind: ResourceAssociation,
    basis: str,
) -> RelatedMetric:
    step = run.windows.step_seconds
    rc = run.config.related
    onset = _onset(cand, window, _steps(rc.max_lag_minutes, step), run.config.max_merge_gap_steps)
    coefficient = outcome.coefficient
    direction = None
    if coefficient is not None:
        direction = RelatedDirection.SAME if coefficient >= 0 else RelatedDirection.OPPOSITE
    lag_seconds = None if outcome.lag_steps is None else outcome.lag_steps * step
    lead = None if onset is None else (window.episode.start - onset) * step
    s = cand.ev.series

    return RelatedMetric(
        comparison_id=comparison_id(f.finding_id, s.entity.key, s.rule.name),
        finding_id=f.finding_id,
        rank=rank,
        anchor=_side(run, anchor, window, f.finding_id),
        candidate=_side(run, cand, window, _candidate_finding(cand, window)),
        selection=_selection(kind, outcome, direction, lag_seconds),
        association=kind,
        association_basis=basis,
        shared_input=outcome.shared_input,
        method=outcome.method,
        window=TimeRange(start=_at(run, window.lo), end=_at(run, window.hi)),
        step_seconds=step,
        coefficient=coefficient,
        direction=direction,
        timing=outcome.timing,
        lag_seconds=lag_seconds,
        onset_lead_seconds=lead,
        candidate_onset=None if onset is None else _at(run, onset),
        overlap_steps=outcome.overlap,
        paired_samples=outcome.paired,
        paired_coverage=round(outcome.paired / window.steps, 6),
        longest_gap_seconds=outcome.longest_gap * step,
        reasons=[*window.reasons, *outcome.reasons],
    )


def _candidate_finding(cand: _Prepared, window: _Window) -> str | None:
    for ep, fid in sorted(cand.findings, key=lambda e: e[0].start):
        if ep.start < window.hi and ep.end > window.lo:
            return fid
    return None


_ASSOCIATION_TEXT = {
    ResourceAssociation.SAME_ENTITY: "Same resource",
    ResourceAssociation.SAME_SERVICE: "Same service",
    ResourceAssociation.SAME_DATABASE_SERVER: "Same database server",
    ResourceAssociation.SAME_STORAGE_CLUSTER: "Same storage cluster",
    ResourceAssociation.SHARED_HOST: "Verified shared host",
    ResourceAssociation.UNKNOWN: "Resource association unknown",
}


def _selection(
    kind: ResourceAssociation,
    outcome: _Outcome,
    direction: RelatedDirection | None,
    lag_seconds: int | None,
) -> str:
    parts = [_ASSOCIATION_TEXT[kind]]
    if outcome.coefficient is not None:
        moves = "together" if direction is RelatedDirection.SAME else "in opposite directions"
        parts.append(f"deviations move {moves} (r = {outcome.coefficient:+.2f})")
    else:
        parts.append(f"unusual for {outcome.overlap} steps around the finding")
    match outcome.timing:
        case ComparisonTiming.CANDIDATE_LEADS:
            parts.append(f"changes about {abs(lag_seconds or 0) // 60} min earlier")
        case ComparisonTiming.CANDIDATE_LAGS:
            parts.append(f"changes about {abs(lag_seconds or 0) // 60} min later")
        case ComparisonTiming.INCONCLUSIVE:
            parts.append("order unclear")
        case ComparisonTiming.SIMULTANEOUS:
            pass
    return "; ".join(parts) + "."


def _side(run: _Run, p: _Prepared, window: _Window, finding: str | None) -> RelatedSeries:
    s = p.ev.series
    span = f"{window.lo}:{window.hi}"
    chart = related_chart_id(run.key, s.entity.key, s.rule.name, span)
    if chart not in run.charts:
        run.charts[chart] = _chart(run, chart, p, window)
    return RelatedSeries(
        source=p.source,
        family=s.rule.family,
        signal=s.rule.name,
        label=s.rule.label,
        entity=s.entity,
        unit=s.unit,
        finding_id=finding,
        chart_id=chart,
    )


def _chart(run: _Run, chart_id: str, p: _Prepared, window: _Window) -> RelatedChart:
    ev, lo, hi = p.ev, window.lo, window.hi
    spread = run.config.z_threshold * ev.scale

    def band(a: NDArray[np.float64]) -> list[float | None] | None:
        values = [chart_value(v) for v in a[lo:hi]]
        return values if any(v is not None for v in values) else None

    return RelatedChart(
        chart_id=chart_id,
        series=slice_series(
            ev.series,
            ev.series.values[ev.offset + lo : ev.offset + hi],
            _at(run, lo),
            run.windows.step_seconds,
        ),
        expected=band(ev.median),
        lower=band(np.maximum(0.0, ev.median - spread)),
        upper=band(ev.median + spread),
        deviation=band(np.round(p.deviation, 3)) if p.is_level else None,
    )
