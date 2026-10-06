"""Opening a Cloudflare source from its connection, and its connection test."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.domain.common import STEP_SECONDS, Scope, SourceKind
from app.domain.metrics import CapabilityStatus
from app.domain.projects import CloudflareConnection, ConnectionTest, synthetic_url
from app.domain.report import AnalysisWindows
from app.sources.cloudflare.api import SECONDS_PER_DAY, CloudflareApi
from app.sources.cloudflare.client import CloudflareClient
from app.sources.cloudflare.source import CloudflareMetricsSource
from app.sources.cloudflare.synthetic import SyntheticCloudflareApi
from app.sources.probe import guarded, summarise_families


@asynccontextmanager
async def connect_cloudflare(
    conn: CloudflareConnection, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[CloudflareMetricsSource]:
    scenario = synthetic_url(conn.api_url)
    api: CloudflareApi = (
        SyntheticCloudflareApi(scenario, conn.zone_id, conn.hostnames)
        if scenario is not None
        else CloudflareClient(conn, transport=transport)
    )
    try:
        yield CloudflareMetricsSource(api, conn.zone_id, conn.hostnames)
    finally:
        await api.aclose()


async def _measure(source: CloudflareMetricsSource, scope: Scope, now: datetime) -> ConnectionTest:
    at = int(now.timestamp()) // STEP_SECONDS * STEP_SECONDS
    end = datetime.fromtimestamp(at, UTC)
    windows = AnalysisWindows.for_end(end)
    capabilities = await source.capabilities(scope, windows)
    settings = await source.settings()
    page_size = min(s.max_page_size for s in settings.values())
    latest = await source.api.traffic(at - SECONDS_PER_DAY, at, page_size)
    requests = int(sum(latest.values.get("cf_requests", {}).values()))

    history = next(
        (
            c.history_days
            for c in capabilities
            if c.status is CapabilityStatus.SUPPORTED and c.history_days is not None
        ),
        None,
    )
    if source.api.backend == "synthetic":
        message = "Synthetic demo source; no real Cloudflare data is queried."
    elif not requests:
        message = "No requests in the last 24 h" + (
            " for these hostnames." if source.hostnames else "."
        )
    else:
        message = f"{requests:,} requests in the last 24 h."
    if source.zone_name is not None:
        message = f"{source.zone_name}: {message}"
    elif source.api.backend != "synthetic":
        message += " Zone name unavailable: add Zone:Read to the token to show the domain."
    return ConnectionTest(
        kind=SourceKind.CLOUDFLARE,
        reachable=True,
        auth_ok=True,
        matched_series=requests,
        history_days=history,
        families=summarise_families(capabilities),
        message=message,
        checked_at=now,
    )


async def probe_cloudflare(source: CloudflareMetricsSource, scope: Scope) -> ConnectionTest:
    return await guarded(SourceKind.CLOUDFLARE, lambda now: _measure(source, scope, now))
