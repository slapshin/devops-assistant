"""Daily event totals of a source: how many events (errors, alerts) each of the 14 trend days
had, summed over every analysed entity.

A day's count sums only known buckets; its coverage says how much of the day was known, so a
partly fetched day is shown as a lower bound, never as quiet.
"""

from collections.abc import Callable, Sequence

from app.domain.common import SourceKind
from app.domain.metrics import MetricSeries
from app.domain.report import TREND_DAYS, DailyCount, EventTotal

SECONDS_PER_DAY = 86400

Events = Callable[[MetricSeries, int, int], float]
"""Events of a series in the day [day_start, day_end)."""


def day_ranges(end: int) -> list[tuple[int, int]]:
    """[start, end) of each trend day, the latest first."""
    return [(end - (i + 1) * SECONDS_PER_DAY, end - i * SECONDS_PER_DAY) for i in range(TREND_DAYS)]


def _buckets(series: MetricSeries, day_start: int, day_end: int) -> list[float | None]:
    start, step = int(series.start.timestamp()), series.step_seconds
    lo = max(0, (day_start - start) // step)
    return series.values[lo : max(lo, (day_end - start) // step)]


def rate_events(series: MetricSeries, day_start: int, day_end: int) -> float:
    """Events of a per-second series: each known bucket's rate times the step."""
    values = _buckets(series, day_start, day_end)
    return sum(v for v in values if v is not None) * series.step_seconds


def event_total(
    source: SourceKind,
    signal: str,
    title: str,
    series: Sequence[MetricSeries],
    end: int,
    events: Events = rate_events,
) -> EventTotal | None:
    """Daily events of ``series`` (on one grid) up to ``end``; None without series."""
    if not series:
        return None
    days = []
    for index, (day_start, day_end) in enumerate(day_ranges(end)):
        count = 0.0
        known = buckets = 0
        for s in series:
            buckets += (day_end - day_start) // s.step_seconds
            known += sum(v is not None for v in _buckets(s, day_start, day_end))
            count += events(s, day_start, day_end)
        days.append(
            DailyCount(
                bucket_index=index,
                count=round(count) if known else None,
                coverage=known / buckets if buckets else 0.0,
            )
        )
    return EventTotal(source=source, signal=signal, title=title, entities=len(series), days=days)
