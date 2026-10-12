"""Opening a Grafana source from its connection, and its connection test."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.domain.common import STEP_SECONDS, Scope, SourceKind
from app.domain.projects import ConnectionTest, GrafanaConnection, synthetic_url
from app.domain.report import AnalysisWindows
from app.sources.grafana.api import GrafanaApi
from app.sources.grafana.catalog import State, classify
from app.sources.grafana.client import GrafanaClient
from app.sources.grafana.source import GrafanaMetricsSource
from app.sources.grafana.synthetic import SyntheticGrafanaApi
from app.sources.probe import guarded, summarise_families


@asynccontextmanager
async def connect_grafana(
    conn: GrafanaConnection, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncGenerator[GrafanaMetricsSource]:
    scenario = synthetic_url(conn.api_url)
    api: GrafanaApi = (
        SyntheticGrafanaApi(scenario)
        if scenario is not None
        else GrafanaClient(conn, transport=transport)
    )
    try:
        yield GrafanaMetricsSource(
            api, conn.folders, conn.rule_uids, conn.rule_labels, conn.instance_labels
        )
    finally:
        await api.aclose()


async def _measure(source: GrafanaMetricsSource, scope: Scope, now: datetime) -> ConnectionTest:
    at = int(now.timestamp()) // STEP_SECONDS * STEP_SECONDS
    windows = AnalysisWindows.for_end(datetime.fromtimestamp(at, UTC))
    capabilities = await source.capabilities(scope, windows)
    selection = await source.selection()
    instances = sum(source.matching_instances(rule) for rule in selection.rules)
    firing = [
        rule
        for rule in selection.rules
        if any(
            classify(i.state) is State.FIRING for i in rule.instances if source.matches(i.labels)
        )
    ]
    more = "+" if selection.truncated else ""
    if source.api.backend == "synthetic":
        message = "Synthetic demo source; no real Grafana alerts are queried."
    elif not selection.rules:
        message = "No Grafana-managed alert rule matches the selection."
    else:
        message = (
            f"{len(selection.rules)}{more} alert rules selected, {instances:,} matching "
            f"instances, {len(firing)} rules firing now."
        )
    version = await source.api.version()
    if version and source.api.backend != "synthetic":
        message += f" Grafana {version}."
    return ConnectionTest(
        kind=SourceKind.GRAFANA,
        reachable=True,
        auth_ok=True,
        matched_series=instances,
        families=summarise_families(capabilities),
        message=message,
        checked_at=now,
    )


async def probe_grafana(source: GrafanaMetricsSource, scope: Scope) -> ConnectionTest:
    return await guarded(SourceKind.GRAFANA, lambda now: _measure(source, scope, now))
