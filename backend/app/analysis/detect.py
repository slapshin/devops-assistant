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
from app.domain.detector_config import DetectorConfig, SignalThresholds
from app.domain.findings import BaselineMode
from app.domain.report import TREND_DAYS

TREND_STEPS = TREND_DAYS * STEPS_PER_DAY


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
    """0 = none, 1 = high heuristic, 2 = critical heuristic."""
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


def _thresholds(ev: AnalysisSeries, config: DetectorConfig) -> SignalThresholds | None:
    key = ev.rule.thresholds
    return config.signals.get(key) if key else None


def evaluate(series: AnalysisSeries, config: DetectorConfig) -> SeriesEvaluation:
    n = len(series.values)
    offset = n - TREND_STEPS
    if offset < 0:
        raise ValueError(f"series shorter than the trend range: {n} < {TREND_STEPS}")
    values = series.values[offset:]
    median = np.full(TREND_STEPS, np.nan)
    scale = np.full(TREND_STEPS, np.nan)
    baselines: list[Baseline] = []
    th = _thresholds(series, config)
    for b in range(TREND_DAYS):
        lo = offset + (TREND_DAYS - 1 - b) * STEPS_PER_DAY
        base = (
            bucket_baseline(series.values, lo, config)
            if series.rule.kind is RuleKind.LEVEL
            else Baseline(
                False, 0, None, np.full(STEPS_PER_DAY, np.nan), np.full(STEPS_PER_DAY, np.nan)
            )
        )
        baselines.append(base)
        sl = slice(lo - offset, lo - offset + STEPS_PER_DAY)
        median[sl] = base.median
        if base.available and th is not None:
            floor = np.maximum(th.abs_floor, config.relative_scale_floor * np.abs(base.median))
            scale[sl] = np.maximum(base.scale, floor)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (values - median) / scale
    rel = np.zeros(TREND_STEPS, dtype=bool)
    abs_level = np.zeros(TREND_STEPS, dtype=np.int8)
    event = np.zeros(TREND_STEPS, dtype=bool)
    observed = ~np.isnan(values)
    kind = series.rule.kind
    if kind is RuleKind.EVENT:
        event = observed & (np.nan_to_num(values) >= 0.5)
    elif kind is RuleKind.SHORTFALL:
        event = observed & (np.nan_to_num(values) >= 1)
    elif th is not None:
        with np.errstate(invalid="ignore"):
            up = _effect(values, median, z, th, config, up=True)
            down = _effect(values, median, z, th, config, up=False)
        direction = series.rule.direction
        rel = (
            observed
            & ~np.isnan(z)
            & (up if direction is Dir.UP else down if direction is Dir.DOWN else (up | down))
        )
        if th.absolute_high is not None:
            abs_level[observed & (np.nan_to_num(values) >= th.absolute_high)] = 1
        if th.absolute_critical is not None:
            abs_level[observed & (np.nan_to_num(values) >= th.absolute_critical)] = 2
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
    absl = ev.abs_level > 0
    flag = rel | absl | (ev.event_flag > 0)
    runs = _runs(flag)
    merged: list[list[int]] = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= config.max_merge_gap_steps:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    abs_min_steps = max(1, (th.absolute_min_minutes if th else 15) * 60 // 300)
    shortfall_steps = max(1, config.shortfall_min_minutes * 60 // 300)
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
        level = int(ev.abs_level[seg].max()) if longest_abs >= abs_min_steps else 0
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


def severity_points(ep: Episode, ev: SeriesEvaluation) -> int:
    rule = ev.series.rule
    magnitude = 0
    if ep.rel_steps and ep.peak_z is not None:
        z = abs(ep.peak_z)
        magnitude = 3 if z >= 10 else 2 if z >= 6 else 1
    if ep.abs_level:
        magnitude = max(magnitude, 3 + (ep.abs_level - 1))
    if rule.kind is not RuleKind.LEVEL:
        return rule.event_points
    minutes = (ep.end - ep.start) * 5
    duration = 2 if minutes >= 120 else 1 if minutes >= 30 else 0
    return magnitude + duration


def baseline_mode(ev: SeriesEvaluation, t: int) -> BaselineMode | None:
    return ev.baseline_for_step(t).mode
