"""Bounded, read-only connection test for a project's draft or stored Prometheus source."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from app.domain.projects import ConnectionTest, LabelMatcher
from app.metrics.client import PrometheusClient, SourceError, SourceErrorKind
from app.metrics.promql import render_matchers

PROBE_TIMEOUT_SECONDS = 20
HISTORY_PROBE_DAYS = 30
HISTORY_PROBE_STEP_SECONDS = 3600
SECONDS_PER_DAY = 86400
UNREACHABLE_KINDS = (SourceErrorKind.UNAVAILABLE, SourceErrorKind.TIMEOUT)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def _measure(
    client: PrometheusClient, matchers: Sequence[LabelMatcher], at: int
) -> ConnectionTest:
    m = render_matchers(matchers)
    result = await client.query(f"count({{{m}}})", at)
    matched = int(result[0].samples[0][1] or 0) if result else 0

    history = await client.query_range(
        f"count(up{{{m}}})",
        at - HISTORY_PROBE_DAYS * SECONDS_PER_DAY,
        at,
        HISTORY_PROBE_STEP_SECONDS,
    )
    stamps = [ts for r in history for ts, v in r.samples if v]
    history_days = round((at - min(stamps)) / SECONDS_PER_DAY, 2) if stamps else None

    message = None if matched else "No series currently match these labels."
    return ConnectionTest(
        reachable=True,
        auth_ok=True,
        matched_series=matched,
        history_days=history_days,
        message=message,
        checked_at=_now(),
    )


async def probe_prometheus(
    client: PrometheusClient, matchers: Sequence[LabelMatcher]
) -> ConnectionTest:
    now = _now()
    try:
        return await asyncio.wait_for(
            _measure(client, matchers, int(now.timestamp())), PROBE_TIMEOUT_SECONDS
        )
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


def probe_synthetic(scenario: str) -> ConnectionTest:
    return ConnectionTest(
        reachable=True,
        auth_ok=True,
        message=f"Synthetic demo source (scenario {scenario!r}); no real metrics are queried.",
        checked_at=_now(),
    )
