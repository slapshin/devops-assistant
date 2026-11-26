"""Read-only connection test shared by every source kind: error mapping and family summary."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.domain.common import SignalFamily, SourceKind
from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.metrics import CapabilityStatus, MetricCapability
from app.domain.projects import ConnectionTest, FamilyCapability

PROBE_TIMEOUT_SECONDS = 30
"""End-to-end bound of one connection test (open, measure, close); see SourceConnections."""
UNREACHABLE_KINDS = (SourceErrorKind.UNAVAILABLE, SourceErrorKind.TIMEOUT)
STATUS_RANK = {
    CapabilityStatus.SUPPORTED: 3,
    CapabilityStatus.PARTIAL: 2,
    CapabilityStatus.UNVERIFIED: 1,
    CapabilityStatus.UNSUPPORTED: 0,
}


def probe_time() -> datetime:
    """The ``checked_at`` of a connection test started now."""
    return datetime.now(UTC).replace(microsecond=0)


def timed_out(kind: SourceKind, checked_at: datetime) -> ConnectionTest:
    """The failed test of a source that did not answer within ``PROBE_TIMEOUT_SECONDS``."""
    return ConnectionTest(
        kind=kind,
        reachable=False,
        auth_ok=None,
        message=f"No answer within {PROBE_TIMEOUT_SECONDS} s.",
        checked_at=checked_at,
    )


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
    """Run a connection test; source errors become a failed test.

    The time bound is not applied here: SourceConnections bounds the whole test, opening and
    closing the source included.
    """
    now = probe_time()
    try:
        return await measure(now)
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
