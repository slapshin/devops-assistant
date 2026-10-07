"""Opening a Sentry source from its connection, and its connection test."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.domain.common import STEP_SECONDS, Scope, SourceKind
from app.domain.metrics import CapabilityStatus
from app.domain.projects import ConnectionTest, SentryConnection, synthetic_url
from app.domain.report import AnalysisWindows
from app.sources.probe import guarded, summarise_families
from app.sources.sentry.api import SentryApi
from app.sources.sentry.catalog import Query
from app.sources.sentry.client import SentryClient
from app.sources.sentry.source import SentryMetricsSource
from app.sources.sentry.synthetic import SyntheticSentryApi


@asynccontextmanager
async def connect_sentry(
    conn: SentryConnection, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncGenerator[SentryMetricsSource]:
    scenario = synthetic_url(conn.api_url)
    api: SentryApi = (
        SyntheticSentryApi(scenario, conn.organization, conn.projects, conn.environment, conn.tags)
        if scenario is not None
        else SentryClient(conn, transport=transport)
    )
    try:
        yield SentryMetricsSource(
            api, conn.organization, conn.projects, conn.environment, conn.tags
        )
    finally:
        await api.aclose()


def _total(source: SentryMetricsSource, query: Query, signal: str) -> int:
    chunk = source.last_day.get(query)
    return int(sum(chunk.values.get(signal, {}).values())) if chunk else 0


async def _measure(source: SentryMetricsSource, scope: Scope, now: datetime) -> ConnectionTest:
    at = int(now.timestamp()) // STEP_SECONDS * STEP_SECONDS
    windows = AnalysisWindows.for_end(datetime.fromtimestamp(at, UTC))
    capabilities = await source.capabilities(scope, windows)
    errors = _total(source, Query.ERRORS, "sentry_errors")
    transactions = _total(source, Query.TRANSACTIONS, "sentry_transactions")

    history = next(
        (
            c.history_days
            for c in capabilities
            if c.status is CapabilityStatus.SUPPORTED and c.history_days is not None
        ),
        None,
    )
    count = len(source.projects)
    scope_text = f" in {count} projects" if count > 1 else ""
    if source.environment:
        scope_text += f" ({source.environment})"
    if source.api.backend == "synthetic":
        message = "Synthetic demo source; no real Sentry data is queried."
    elif not errors and not transactions:
        message = f"No error events or transactions{scope_text} in the last 24 h."
    else:
        message = (
            f"{errors:,} error events and {transactions:,} transactions{scope_text} "
            "in the last 24 h."
        )
    return ConnectionTest(
        kind=SourceKind.SENTRY,
        reachable=True,
        auth_ok=True,
        matched_series=errors + transactions,
        history_days=history,
        families=summarise_families(capabilities),
        message=message,
        checked_at=now,
    )


async def probe_sentry(source: SentryMetricsSource, scope: Scope) -> ConnectionTest:
    return await guarded(SourceKind.SENTRY, lambda now: _measure(source, scope, now))
