"""Bounded, read-only connection test for a project's draft or stored metrics source."""

import asyncio
from datetime import UTC, datetime

from app.domain.common import STEP_SECONDS, Scope, SignalFamily
from app.domain.interfaces import MetricsSource
from app.domain.metrics import CapabilityStatus, MetricCapability
from app.domain.projects import ConnectionTest, FamilyCapability
from app.domain.report import AnalysisWindows
from app.metrics.client import PrometheusClient, SourceError, SourceErrorKind
from app.metrics.promql import scope_matchers

PROBE_TIMEOUT_SECONDS = 30
HISTORY_PROBE_DAYS = 30
HISTORY_PROBE_STEP_SECONDS = 3600
SECONDS_PER_DAY = 86400
UNREACHABLE_KINDS = (SourceErrorKind.UNAVAILABLE, SourceErrorKind.TIMEOUT)
STATUS_RANK = {
    CapabilityStatus.SUPPORTED: 3,
    CapabilityStatus.PARTIAL: 2,
    CapabilityStatus.UNVERIFIED: 1,
    CapabilityStatus.UNSUPPORTED: 0,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def summarise_families(capabilities: list[MetricCapability]) -> list[FamilyCapability]:
    """Best status per family; the reason comes from that family's best signal."""
    best: dict[SignalFamily, MetricCapability] = {}
    for cap in capabilities:
        current = best.get(cap.family)
        if current is None or STATUS_RANK[cap.status] > STATUS_RANK[current.status]:
            best[cap.family] = cap
    return [
        FamilyCapability(family=family, status=best[family].status, reason=best[family].reason)
        for family in SignalFamily
        if family in best
    ]


async def _matched_series(client: PrometheusClient, scope: Scope, at: int) -> int:
    result = await client.query(f"count({{{scope_matchers(scope)}}})", at)
    return int(result[0].samples[0][1] or 0) if result else 0


async def _history_days(client: PrometheusClient, scope: Scope, at: int) -> float | None:
    history = await client.query_range(
        f"count(up{{{scope_matchers(scope)}}})",
        at - HISTORY_PROBE_DAYS * SECONDS_PER_DAY,
        at,
        HISTORY_PROBE_STEP_SECONDS,
    )
    stamps = [ts for r in history for ts, v in r.samples if v]
    return round((at - min(stamps)) / SECONDS_PER_DAY, 2) if stamps else None


async def _measure(
    source: MetricsSource, client: PrometheusClient | None, scope: Scope, now: datetime
) -> ConnectionTest:
    at = int(now.timestamp()) // STEP_SECONDS * STEP_SECONDS
    matched = history = None
    if client is not None:  # fail fast on reachability and auth before capability discovery
        matched = await _matched_series(client, scope, at)
        history = await _history_days(client, scope, at)

    windows = AnalysisWindows.for_end(datetime.fromtimestamp(at, UTC))
    families = summarise_families(await source.capabilities(scope, windows))
    if client is None:
        message = "Synthetic demo source; no real metrics are queried."
    elif not matched:
        message = "No series currently match these labels."
    else:
        message = None
    return ConnectionTest(
        reachable=True,
        auth_ok=True,
        matched_series=matched,
        history_days=history,
        families=families,
        message=message,
        checked_at=now,
    )


async def probe(
    source: MetricsSource, client: PrometheusClient | None, scope: Scope
) -> ConnectionTest:
    now = _now()
    try:
        return await asyncio.wait_for(_measure(source, client, scope, now), PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return ConnectionTest(
            reachable=False,
            auth_ok=None,
            message=f"No answer within {PROBE_TIMEOUT_SECONDS} s.",
            checked_at=now,
        )
    except SourceError as exc:
        if exc.kind is SourceErrorKind.AUTH:
            return ConnectionTest(
                reachable=True, auth_ok=False, message=exc.message, checked_at=now
            )
        reachable = exc.kind not in UNREACHABLE_KINDS
        return ConnectionTest(
            reachable=reachable,
            auth_ok=True if reachable else None,
            message=f"{exc.kind.value}: {exc.message}",
            checked_at=now,
        )
