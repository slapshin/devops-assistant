"""Connection test of a Prometheus-compatible source: matching series, history, families."""

from datetime import UTC, datetime

from app.domain.common import STEP_SECONDS, Scope, SourceKind
from app.domain.interfaces import MetricsSource
from app.domain.projects import ConnectionTest
from app.domain.report import AnalysisWindows
from app.sources.probe import guarded, summarise_families
from app.sources.prometheus.client import PrometheusClient
from app.sources.prometheus.promql import scope_matchers

HISTORY_PROBE_DAYS = 30
HISTORY_PROBE_STEP_SECONDS = 3600
SECONDS_PER_DAY = 86400


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
    return await guarded(SourceKind.PROMETHEUS, lambda now: _measure(source, client, scope, now))
