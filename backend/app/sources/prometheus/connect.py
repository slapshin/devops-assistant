"""Opening a Prometheus-compatible source from its connection, and its connection test
(matching series, history, families)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.domain.common import STEP_SECONDS, Scope, SourceKind
from app.domain.projects import ConnectionTest, PrometheusConnection, synthetic_url
from app.domain.report import AnalysisWindows
from app.sources.probe import guarded, summarise_families
from app.sources.prometheus.client import PrometheusClient
from app.sources.prometheus.promql import scope_matchers
from app.sources.prometheus.source import PrometheusMetricsSource
from app.sources.prometheus.synthetic import SyntheticMetricsSource

type PrometheusSource = PrometheusMetricsSource | SyntheticMetricsSource

HISTORY_PROBE_DAYS = 30
HISTORY_PROBE_STEP_SECONDS = 3600
SECONDS_PER_DAY = 86400


@asynccontextmanager
async def connect_prometheus(
    conn: PrometheusConnection, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncGenerator[PrometheusSource]:
    if (scenario := synthetic_url(conn.url)) is not None:
        yield SyntheticMetricsSource(scenario, conn.custom_rules)
        return

    client = PrometheusClient.from_connection(conn, transport=transport)
    try:
        yield PrometheusMetricsSource(client, custom_rules=conn.custom_rules)
    finally:
        await client.aclose()


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


async def _measure(source: PrometheusSource, scope: Scope, now: datetime) -> ConnectionTest:
    client = source.client if isinstance(source, PrometheusMetricsSource) else None
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


async def probe_prometheus(source: PrometheusSource, scope: Scope) -> ConnectionTest:
    return await guarded(SourceKind.PROMETHEUS, lambda now: _measure(source, scope, now))
