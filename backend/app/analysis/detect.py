"""Per-series anomaly flags over the 14 trend buckets and episode extraction.

Coordinates: the trend range is the last 14 days of the 28-day grid, indexed t = 0..4031;
bucket b (0 = latest day) covers t in [(13 - b) * 288, (14 - b) * 288).
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.analysis.baseline import STEPS_PER_DAY, Baseline, bucket_baseline
from app.analysis.derive import AnalysisSeries, Array
from app.analysis.rules import Dir, RuleKind
from app.domain.common import STEP_SECONDS
from app.domain.detector_config import DetectorConfig, SignalThresholds
from app.domain.findings import BaselineMode
from app.domain.report import TREND_DAYS

TREND_STEPS = TREND_DAYS * STEPS_PER_DAY
SECONDS_PER_MINUTE = 60
MINUTES_PER_STEP = STEP_SECONDS // SECONDS_PER_MINUTE

EVENT_MIN_VALUE = 0.5
"""Counts per step; anything that rounds to at least one event is anomalous."""
SHORTFALL_MIN_VALUE = 1.0
"""At least one replica missing."""

ABS_LEVEL_NONE = 0
ABS_LEVEL_HIGH = 1
ABS_LEVEL_CRITICAL = 2
DEFAULT_ABSOLUTE_MIN_MINUTES = 15
"""Minimum absolute-breach duration for rules without signal thresholds."""

# Severity points: (minimum |z| or minutes, points), checked from the top.
Z_MAGNITUDE_POINTS = ((10.0, 3), (6.0, 2))
Z_MAGNITUDE_MIN_POINTS = 1
ABS_HIGH_POINTS = 3
"""An absolute high breach scores like |z| >= 10; each level above adds one point."""
DURATION_POINTS = ((120, 2), (30, 1))


@dataclass
class SeriesEvaluation:
    series: AnalysisSeries
    offset: int
    """Grid index of trend step 0."""
    values: Array
    median: Array
    scale: Array
    z: Array
    rel_flag: Array
    abs_level: Array
    """ABS_LEVEL_NONE, ABS_LEVEL_HIGH or ABS_LEVEL_CRITICAL (heuristic thresholds)."""
    event_flag: Array
    baselines: list[Baseline]
    """Per bucket index (0 = latest day)."""
    episodes: list[Episode] = field(default_factory=list)

    def baseline_for_step(self, t: int) -> Baseline:
        return self.baselines[TREND_DAYS - 1 - t // STEPS_PER_DAY]


@dataclass
class Episode:
    start: int
    end: int
    """Exclusive, trend coordinates."""
    anomalous_steps: int
    peak: int
    rel_steps: int
    abs_level: int
    peak_z: float | None
    up: bool
    severity_points: int = 0

    def buckets(self) -> set[int]:
        return {TREND_DAYS - 1 - t // STEPS_PER_DAY for t in range(self.start, self.end)}


def _thresholds(series: AnalysisSeries, config: DetectorConfig) -> SignalThresholds | None:
    key = series.rule.thresholds
    return config.signals.get(key) if key else None


def _minutes_to_steps(minutes: int) -> int:
    return max(1, minutes * SECONDS_PER_MINUTE // STEP_SECONDS)


def evaluate(series: AnalysisSeries, config: DetectorConfig) -> SeriesEvaluation:
    n = len(series.values)
    offset = n - TREND_STEPS
    if offset < 0:
        raise ValueError(
            f"series {series.rule.name} {series.entity.key} shorter than the trend range: "
            f"{n} < {TREND_STEPS}"
        )

    values = series.values[offset:]
    th = _thresholds(series, config)
    baselines, median, scale = _bucket_baselines(series, offset, th, config)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (values - median) / scale

    rel, abs_level, event = _flags(series, values, median, z, th, config)

    ev = SeriesEvaluation(
        series,
        offset,
        values,
        median,
        scale,
        z,
        rel.astype(np.float64),
        abs_level.astype(np.float64),
        event.astype(np.float64),
        baselines,
    )
    ev.episodes = _episodes(ev, th, config)
    return ev


def _bucket_baselines(
    series: AnalysisSeries, offset: int, th: SignalThresholds | None, config: DetectorConfig
) -> tuple[list[Baseline], Array, Array]:
    """Per-bucket baselines, and the expected median and z-score scale per trend step."""
    median = np.full(TREND_STEPS, np.nan)
    scale = np.full(TREND_STEPS, np.nan)
    baselines: list[Baseline] = []
    for b in range(TREND_DAYS):
        bucket_start = offset + (TREND_DAYS - 1 - b) * STEPS_PER_DAY
        if series.rule.kind is RuleKind.LEVEL:
            base = bucket_baseline(series.values, bucket_start, config)
        else:
            unavailable = np.full(STEPS_PER_DAY, np.nan)
            base = Baseline(False, 0, None, unavailable, unavailable.copy())
        baselines.append(base)

        bucket = slice(bucket_start - offset, bucket_start - offset + STEPS_PER_DAY)
        median[bucket] = base.median
        if base.available and th is not None:
            # Floor the scale so near-constant baselines (MAD ~ 0) do not turn noise into z > 4.
            floor = np.maximum(th.abs_floor, config.relative_scale_floor * np.abs(base.median))
            scale[bucket] = np.maximum(base.scale, floor)
    return baselines, median, scale


def _flags(
    series: AnalysisSeries,
    values: Array,
    median: Array,
    z: Array,
    th: SignalThresholds | None,
    config: DetectorConfig,
) -> tuple[NDArray[np.bool_], NDArray[np.int8], NDArray[np.bool_]]:
    """Per-step relative flag, absolute level, and event flag."""
    rel = np.zeros(TREND_STEPS, dtype=bool)
    abs_level = np.zeros(TREND_STEPS, dtype=np.int8)
    event = np.zeros(TREND_STEPS, dtype=bool)
    observed = ~np.isnan(values)
    kind = series.rule.kind

    if kind is RuleKind.EVENT:
        event = observed & (np.nan_to_num(values) >= EVENT_MIN_VALUE)
    elif kind is RuleKind.SHORTFALL:
        event = observed & (np.nan_to_num(values) >= SHORTFALL_MIN_VALUE)
    elif th is not None:
        with np.errstate(invalid="ignore"):
            up = _effect(values, median, z, th, config, up=True)
            down = _effect(values, median, z, th, config, up=False)
        rel = observed & ~np.isnan(z) & _directional(up, down, series.rule.direction)
        if th.absolute_high is not None:
            abs_level[observed & (np.nan_to_num(values) >= th.absolute_high)] = ABS_LEVEL_HIGH
        if th.absolute_critical is not None:
            critical = observed & (np.nan_to_num(values) >= th.absolute_critical)
            abs_level[critical] = ABS_LEVEL_CRITICAL
    return rel, abs_level, event


def _directional(
    up: NDArray[np.bool_], down: NDArray[np.bool_], direction: Dir
) -> NDArray[np.bool_]:
    match direction:
        case Dir.UP:
            return up
        case Dir.DOWN:
            return down
        case _:
            return up | down


def _effect(
    x: Array, median: Array, z: Array, th: SignalThresholds, config: DetectorConfig, *, up: bool
) -> NDArray[np.bool_]:
    sign = 1.0 if up else -1.0
    ok = (sign * z) >= config.z_threshold
    diff = sign * (x - median)
    if th.min_abs_effect is not None:
        ok &= diff >= th.min_abs_effect
    if th.min_rel_factor is not None:
        ok &= (x >= median * th.min_rel_factor) if up else (x * th.min_rel_factor <= median)
    return np.asarray(ok, dtype=bool)


def _runs(mask: NDArray[np.bool_] | Array) -> list[tuple[int, int]]:
    padded = np.concatenate([[0], mask.astype(np.int8), [0]])
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def _episodes(
    ev: SeriesEvaluation, th: SignalThresholds | None, config: DetectorConfig
) -> list[Episode]:
    kind = ev.series.rule.kind
    rel = ev.rel_flag > 0
    absl = ev.abs_level > ABS_LEVEL_NONE
    flag = rel | absl | (ev.event_flag > 0)
    merged = _merge_runs(_runs(flag), config.max_merge_gap_steps)

    abs_min_minutes = th.absolute_min_minutes if th else DEFAULT_ABSOLUTE_MIN_MINUTES
    abs_min_steps = _minutes_to_steps(abs_min_minutes)
    shortfall_steps = _minutes_to_steps(config.shortfall_min_minutes)
    episodes: list[Episode] = []
    for s, e in merged:
        seg = slice(s, e)
        n_flag = int(flag[seg].sum())
        n_rel = int(rel[seg].sum())
        longest_abs = max((b - a for a, b in _runs(absl[seg])), default=0)
        if kind is RuleKind.LEVEL:
            valid = n_rel >= config.min_episode_steps or longest_abs >= abs_min_steps
        elif kind is RuleKind.SHORTFALL:
            valid = max((b - a for a, b in _runs(flag[seg])), default=0) >= shortfall_steps
        else:
            valid = n_flag >= 1
        if not valid:
            continue

        level = int(ev.abs_level[seg].max()) if longest_abs >= abs_min_steps else ABS_LEVEL_NONE
        zs = ev.z[seg]
        if kind is RuleKind.LEVEL and n_rel:
            idx = np.where(rel[seg], np.abs(np.nan_to_num(zs)), -1.0)
            peak = s + int(np.argmax(idx))
            peak_z: float | None = float(ev.z[peak])
        else:
            peak = s + int(np.nanargmax(np.nan_to_num(ev.values[seg], nan=-np.inf)))
            peak_z = None if np.isnan(ev.z[peak]) else float(ev.z[peak])
        up = peak_z is None or peak_z >= 0
        episodes.append(Episode(s, e, n_flag, peak, n_rel, level, peak_z, up))

    for ep in episodes:
        ep.severity_points = severity_points(ep, ev)
    return episodes


def _merge_runs(runs: list[tuple[int, int]], max_gap_steps: int) -> list[tuple[int, int]]:
    """Join runs separated by at most ``max_gap_steps`` unflagged steps."""
    merged: list[list[int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] <= max_gap_steps:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def severity_points(ep: Episode, ev: SeriesEvaluation) -> int:
    rule = ev.series.rule
    if rule.kind is not RuleKind.LEVEL:
        return rule.event_points

    magnitude = 0
    if ep.rel_steps and ep.peak_z is not None:
        magnitude = _points_at_least(abs(ep.peak_z), Z_MAGNITUDE_POINTS, Z_MAGNITUDE_MIN_POINTS)
    if ep.abs_level:
        magnitude = max(magnitude, ABS_HIGH_POINTS + (ep.abs_level - ABS_LEVEL_HIGH))

    minutes = (ep.end - ep.start) * MINUTES_PER_STEP
    return magnitude + _points_at_least(minutes, DURATION_POINTS, 0)


def _points_at_least(value: float, thresholds: tuple[tuple[float, int], ...], default: int) -> int:
    """Points of the first (minimum, points) pair that ``value`` reaches."""
    for minimum, points in thresholds:
        if value >= minimum:
            return points
    return default


def baseline_mode(ev: SeriesEvaluation, t: int) -> BaselineMode | None:
    return ev.baseline_for_step(t).mode
