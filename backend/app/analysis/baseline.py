"""Robust per-bucket baselines from preceding days only (median/MAD, optional time-of-day)."""

import warnings
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.analysis.derive import Array
from app.domain.detector_config import DetectorConfig
from app.domain.findings import BaselineMode

STEPS_PER_DAY = 288


@dataclass(frozen=True)
class Baseline:
    available: bool
    days: int
    mode: BaselineMode | None
    median: Array
    """Per-step expected value for the analysed bucket (NaN when unavailable)."""
    scale: Array


def _nanmedian(a: Array, axis: int | None = None) -> Array:
    if not np.isnan(a).any():
        return np.asarray(np.median(a, axis=axis), dtype=np.float64)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.asarray(np.nanmedian(a, axis=axis), dtype=np.float64)


def bucket_baseline(
    values: Array, lo: int, config: DetectorConfig, steps_per_day: int = STEPS_PER_DAY
) -> Baseline:
    """Baseline for the bucket starting at grid index ``lo``: uses only [lo - 14d, lo)."""
    nan = np.full(steps_per_day, np.nan)
    start = max(0, lo - config.baseline_max_days * steps_per_day)
    days = values[start:lo].reshape(-1, steps_per_day)
    if days.size == 0:
        return Baseline(False, 0, None, nan, nan)
    coverage = np.mean(~np.isnan(days), axis=1)
    adequate = days[coverage >= config.baseline_day_min_coverage]
    n = int(adequate.shape[0])
    if n < config.baseline_min_adequate_days:
        return Baseline(False, n, None, nan, nan)
    if n >= config.time_of_day_min_days:
        half = config.time_of_day_half_width_hours * steps_per_day // 24
        padded = np.concatenate([adequate[:, -half:], adequate, adequate[:, :half]], axis=1)
        windows = sliding_window_view(padded, 2 * half + 1, axis=1)[:, :steps_per_day, :]
        pooled = windows.transpose(1, 0, 2).reshape(steps_per_day, -1)
        median = _nanmedian(pooled, axis=1)
        mad = _nanmedian(np.abs(pooled - median[:, None]), axis=1)
        mode = BaselineMode.TIME_OF_DAY
    else:
        flat = adequate.ravel()
        m = float(_nanmedian(flat))
        median = np.full(steps_per_day, m)
        mad = np.full(steps_per_day, float(_nanmedian(np.abs(flat - m))))
        mode = BaselineMode.WHOLE_BASELINE
    return Baseline(True, n, mode, median, config.mad_scale * mad)
