"""Scope diagnostics of a Prometheus source (T022): selected entities and per-matcher checks."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime

from app.domain.common import LabelMatcher, Labels, Reason, Scope, SignalFamily, SourceKind
from app.domain.diagnostics import (
    MAX_AVAILABLE,
    MAX_SAMPLES,
    CountUnit,
    FilterCheck,
    FilterField,
    ScopeDiagnostics,
    SelectedEntity,
    capped_available,
    capped_requests,
    capped_selection,
    counted,
    diagnostic_failed,
)
from app.domain.interfaces import SourceError
from app.domain.metrics import MetricSeries
from app.sources.prometheus.client import PrometheusClient, RangeResult
from app.sources.prometheus.promql import assert_leave_one_out, assert_scoped, render_matchers

SECONDS_PER_DAY = 86400


def series_selection(series: Sequence[MetricSeries]) -> tuple[list[SelectedEntity], int, str]:
    """Entities of the collected series, their total and a summary line."""
    counts: Counter[str] = Counter()
    families: dict[str, set[SignalFamily]] = defaultdict(set)
    first: dict[str, MetricSeries] = {}
    for s in series:
        counts[s.entity.key] += 1
        families[s.entity.key].add(s.family)
        first.setdefault(s.entity.key, s)
    entities = [
        SelectedEntity(
            display_name=s.entity.display_name,
            labels={"kind": s.entity.kind.value, **s.entity.labels},
            count=counts[key],
            unit=CountUnit.SERIES,
            families=sorted(families[key], key=list(SignalFamily).index),
        )
        for key, s in first.items()
    ]
    kinds = Counter(s.entity.kind.value for s in first.values())
    parts = [counted(n, kind.replace("_", " ")) for kind, n in kinds.most_common()]
    summary = ", ".join([*parts, f"{len(series):,} series"])
    return capped_selection(entities), len(entities), summary


def varied_samples(by_signal: Mapping[tuple[SignalFamily, str], Labels]) -> list[Labels]:
    """Up to MAX_SAMPLES label sets, one family at a time before repeating a family."""
    picked: list[Labels] = []
    seen: set[SignalFamily] = set()
    for (family, _), labels in by_signal.items():
        if family not in seen and labels not in picked and len(picked) < MAX_SAMPLES:
            picked.append(dict(labels))
            seen.add(family)
    for labels in by_signal.values():
        if len(picked) >= MAX_SAMPLES:
            break
        if labels not in picked:
            picked.append(dict(labels))
    return picked


def _metric_union(names_regex: str, matchers: Sequence[LabelMatcher]) -> str:
    rest = render_matchers(matchers)
    return f'{{__name__=~"{names_regex}"' + (f", {rest}" if rest else "") + "}"


async def matcher_checks(
    client: PrometheusClient, scope: Scope, end_time: datetime, names_regex: str
) -> tuple[list[FilterCheck], list[Reason], list[str]]:
    """Per scope matcher: series of the catalog metrics with all matchers versus with that
    matcher dropped, and the label's values within the other matchers. Dropping a matcher
    may leave no restricting matcher, so these counts are the only queries that leave the
    scope (``assert_leave_one_out``); they never return series data."""
    at = int(end_time.timestamp())
    checks: list[FilterCheck] = []
    notes: list[Reason] = []
    full = f"count({_metric_union(names_regex, scope.matchers)})"
    assert_scoped(full, scope, ())
    requests = [full]
    try:
        matched: int | None = _count(await client.query(full, at))
    except SourceError as exc:
        notes.append(diagnostic_failed("series count", exc))
        matched = None
    for dropped in scope.matchers:
        others = [m for m in scope.matchers if m != dropped]
        without = f"count({_metric_union(names_regex, others)})"
        match = _metric_union(names_regex, others)
        assert_leave_one_out(without, scope, dropped)
        assert_leave_one_out(match, scope, dropped)
        requests += [without, f"label values {dropped.name} of {match}"]
        matched_without: int | None = None
        available: list[str] = []
        truncated = False
        try:
            matched_without = _count(await client.query(without, at))
            values = await client.label_values(
                dropped.name, [match], at - SECONDS_PER_DAY, at, MAX_AVAILABLE + 1
            )
            available, truncated = capped_available(values)
        except SourceError as exc:
            notes.append(diagnostic_failed(f"check of {dropped}", exc))
        checks.append(
            FilterCheck(
                part=str(dropped),
                field=FilterField.MATCHER,
                matched=matched,
                matched_without=matched_without,
                unit=CountUnit.SERIES,
                available=available,
                available_truncated=truncated,
                matching=[v for v in available if dropped.matches(v)],
            )
        )
    return checks, notes, requests


def _count(results: Sequence[RangeResult]) -> int:
    """``count()`` of nothing returns no sample, which is zero series."""
    return int(results[0].samples[0][1] or 0) if results else 0


def prometheus_diagnostics(
    series: Sequence[MetricSeries],
    samples: list[Labels],
    checks: list[FilterCheck],
    notes: list[Reason],
    requests: list[str],
) -> ScopeDiagnostics:
    selection, total, summary = series_selection(series)
    if not series:
        notes = [
            Reason(code="nothing_selected", message="No series were collected in this scope."),
            *notes,
        ]
    return ScopeDiagnostics(
        kind=SourceKind.PROMETHEUS,
        summary=summary,
        selection=selection,
        selection_total=total,
        samples=samples,
        checks=checks,
        requests=capped_requests(requests),
        notes=notes,
    )
