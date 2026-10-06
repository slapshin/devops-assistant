"""Bounded, read-only connection test shared by every source kind."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.domain.common import SignalFamily, SourceKind
from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.metrics import CapabilityStatus, MetricCapability
from app.domain.projects import ConnectionTest, FamilyCapability

PROBE_TIMEOUT_SECONDS = 30
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


async def guarded(
    kind: SourceKind, measure: Callable[[datetime], Awaitable[ConnectionTest]]
) -> ConnectionTest:
    """Run a bounded connection test; timeouts and source errors become a failed test."""
    now = _now()
    try:
        return await asyncio.wait_for(measure(now), PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return ConnectionTest(
            kind=kind,
            reachable=False,
            auth_ok=None,
            message=f"No answer within {PROBE_TIMEOUT_SECONDS} s.",
            checked_at=now,
        )
    except SourceError as exc:
        if exc.kind is SourceErrorKind.AUTH:
            return ConnectionTest(
                kind=kind, reachable=True, auth_ok=False, message=exc.message, checked_at=now
            )
        reachable = exc.kind not in UNREACHABLE_KINDS
        return ConnectionTest(
            kind=kind,
            reachable=reachable,
            auth_ok=True if reachable else None,
            message=f"{exc.kind.value}: {exc.message}",
            checked_at=now,
        )
