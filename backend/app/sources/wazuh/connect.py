"""Opening a Wazuh source from its connection, and its connection test."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.domain.common import STEP_SECONDS, Scope, SourceKind
from app.domain.metrics import CapabilityStatus
from app.domain.projects import ConnectionTest, WazuhConnection, synthetic_url
from app.domain.report import AnalysisWindows
from app.sources.probe import guarded, summarise_families
from app.sources.wazuh.api import SECONDS_PER_DAY, GroupMembers, WazuhApi
from app.sources.wazuh.client import WazuhClient
from app.sources.wazuh.source import WazuhMetricsSource
from app.sources.wazuh.synthetic import SyntheticWazuhApi


@asynccontextmanager
async def connect_wazuh(
    conn: WazuhConnection, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[WazuhMetricsSource]:
    scenario = synthetic_url(conn.api_url)
    api: WazuhApi = (
        SyntheticWazuhApi(scenario, conn.index_pattern, conn.agents, conn.labels, conn.groups)
        if scenario is not None
        else WazuhClient(conn, transport=transport)
    )
    try:
        yield WazuhMetricsSource(api, conn.index_pattern, conn.agents, conn.labels, conn.groups)
    finally:
        await api.aclose()


async def _measure(source: WazuhMetricsSource, scope: Scope, now: datetime) -> ConnectionTest:
    at = int(now.timestamp()) // STEP_SECONDS * STEP_SECONDS
    windows = AnalysisWindows.for_end(datetime.fromtimestamp(at, UTC))
    capabilities = await source.capabilities(scope, windows)
    last_day = await source.discover(at - SECONDS_PER_DAY, at)
    alerts = sum(a.alerts for a in last_day.agents)
    history = next(
        (
            c.history_days
            for c in capabilities
            if c.status is CapabilityStatus.SUPPORTED and c.history_days is not None
        ),
        None,
    )
    count = len(last_day.agents)
    more = "+" if last_day.truncated else ""
    if source.api.backend == "synthetic":
        message = "Synthetic demo source; no real Wazuh alerts are queried."
    elif not alerts:
        message = "No alerts from the selected agents in the last 24 h."
    else:
        message = f"{alerts:,} alerts from {count}{more} agents in the last 24 h."
    if (members := await source.members(at)) is not None:
        message += " " + _groups_text(members)
    return ConnectionTest(
        kind=SourceKind.WAZUH,
        reachable=True,
        auth_ok=True,
        matched_series=alerts,
        history_days=history,
        families=summarise_families(capabilities),
        message=message,
        checked_at=now,
    )


def _groups_text(members: GroupMembers) -> str:
    """ "Groups: web 3 agents, db none." from the monitoring snapshots."""
    parts = [
        f"{group} {len(names)}{'+' if members.truncated else ''} agents"
        if names
        else f"{group} none"
        for group, names in sorted(members.members.items())
    ]
    return f"Groups: {', '.join(parts)}."


async def probe_wazuh(source: WazuhMetricsSource, scope: Scope) -> ConnectionTest:
    return await guarded(SourceKind.WAZUH, lambda now: _measure(source, scope, now))
