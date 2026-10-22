"""Chart slices of analysed series, persisted as evidence so a report reopens without the source."""

from datetime import datetime
from typing import Final

import numpy as np

from app.analysis.derive import AnalysisSeries
from app.domain.ids import series_id
from app.domain.metrics import MetricSeries

CHART_DIGITS: Final = 5
"""Significant digits of persisted chart values. Full float precision about doubles a report's
download without changing any chart; finding numbers keep full precision."""


def opt(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def chart_value(value: float) -> float | None:
    """A chart value rounded to ``CHART_DIGITS`` significant digits; NaN is a gap."""
    return None if np.isnan(value) else float(f"{value:.{CHART_DIGITS}g}")


def sample_coverage(values: list[float | None]) -> float:
    return sum(v is not None for v in values) / len(values) if values else 0.0


def slice_series(
    s: AnalysisSeries, values: np.ndarray, start: datetime, step_seconds: int
) -> MetricSeries:
    """An analysed series' values from ``start`` on, with its query and identity."""
    vals = [chart_value(v) for v in values]
    return MetricSeries(
        series_id=series_id(s.query, f"{s.entity.key}|{s.rule.name}"),
        family=s.rule.family,
        signal=s.rule.name,
        entity=s.entity,
        labels=dict(s.source.labels),
        unit=s.unit,
        query=s.query,
        step_seconds=step_seconds,
        start=start,
        values=vals,
        coverage=sample_coverage(vals),
    )
