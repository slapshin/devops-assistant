"""Robust per-bucket baselines from preceding days only (median/MAD, optional time-of-day)."""

import warnings
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.analysis.derive import Array
from app.domain.detector_config import DetectorConfig
from app.domain.findings import BaselineMode

STEPS_PER_DAY = 288
HOURS_PER_DAY = 24


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
    values: Array, bucket_start: int, config: DetectorConfig, steps_per_day: int = STEPS_PER_DAY
) -> Baseline:
    """Baseline for the bucket starting at grid index ``bucket_start``.

    Uses only the preceding ``baseline_max_days`` days, so a bucket never sees its own data.
    """
    unavailable = np.full(steps_per_day, np.nan)
    history_start = max(0, bucket_start - config.baseline_max_days * steps_per_day)
    days = values[history_start:bucket_start].reshape(-1, steps_per_day)
    if days.size == 0:
        return Baseline(False, 0, None, unavailable, unavailable)

    coverage = np.mean(~np.isnan(days), axis=1)
    adequate = days[coverage >= config.baseline_day_min_coverage]
    adequate_days = int(adequate.shape[0])
    if adequate_days < config.baseline_min_adequate_days:
        return Baseline(False, adequate_days, None, unavailable, unavailable)

    if adequate_days >= config.time_of_day_min_days:
        median, mad = _time_of_day_median_mad(adequate, config, steps_per_day)
        mode = BaselineMode.TIME_OF_DAY
    else:
        median, mad = _whole_baseline_median_mad(adequate, steps_per_day)
        mode = BaselineMode.WHOLE_BASELINE

    return Baseline(True, adequate_days, mode, median, config.mad_scale * mad)


def _time_of_day_median_mad(
    adequate: Array, config: DetectorConfig, steps_per_day: int
) -> tuple[Array, Array]:
    """Per-step median/MAD pooled over a +/- half-width window around the same time of day."""
    half = config.time_of_day_half_width_hours * steps_per_day // HOURS_PER_DAY
    # Wrap around midnight so steps near the day boundary get a full window.
    padded = np.concatenate([adequate[:, -half:], adequate, adequate[:, :half]], axis=1)
    windows = sliding_window_view(padded, 2 * half + 1, axis=1)[:, :steps_per_day, :]
    pooled = windows.transpose(1, 0, 2).reshape(steps_per_day, -1)

    median = _nanmedian(pooled, axis=1)
    mad = _nanmedian(np.abs(pooled - median[:, None]), axis=1)
    return median, mad


def _whole_baseline_median_mad(adequate: Array, steps_per_day: int) -> tuple[Array, Array]:
    """One median/MAD over every adequate day, broadcast to all steps."""
    flat = adequate.ravel()
    overall_median = float(_nanmedian(flat))
    median = np.full(steps_per_day, overall_median)
    mad = np.full(steps_per_day, float(_nanmedian(np.abs(flat - overall_median))))
    return median, mad
