"""Source connections: opening, probing and previewing a project's data sources (T012, T014).

Every analysis, connection test or rule preview resolves the source's credential, opens its
own clients and closes them afterwards, so editing or deleting a project, or rotating a
credential, never pulls a client out from under a running job. Callers say *what* to do with
a source; which client a kind needs, how it is probed and how it is closed stays here.

Every API operation has an end-to-end bound that covers opening, querying and closing: a
connection test ``PROBE_TIMEOUT_SECONDS``, a rule preview ``PREVIEW_TIMEOUT_SECONDS``. Health
probes a project's sources concurrently, so it is bounded by one connection test, not by
their number. A timeout is a failed result, never an error.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from app.domain.common import STEP_SECONDS, LabelMatcher, Scope, SourceKind
from app.domain.custom_rules import CustomRule, CustomRulePreview
from app.domain.interfaces import MetricsSource, OpenedSource
from app.domain.projects import (
    CloudflareConnection,
    ConnectionTest,
    GrafanaConnection,
    Project,
    PrometheusConnection,
    PrometheusSourceInput,
    SentryConnection,
    SourceConnection,
    SourceInput,
    WazuhConnection,
    synthetic_url,
)
from app.sources.cloudflare.connect import connect_cloudflare, probe_cloudflare
from app.sources.cloudflare.synthetic import SCENARIOS as CLOUDFLARE_SCENARIOS
from app.sources.grafana.connect import connect_grafana, probe_grafana
from app.sources.grafana.synthetic import SCENARIOS as GRAFANA_SCENARIOS
from app.sources.probe import PROBE_TIMEOUT_SECONDS, probe_time, timed_out
from app.sources.prometheus.connect import connect_prometheus, probe_prometheus
from app.sources.prometheus.source import failed_preview
from app.sources.prometheus.synthetic import SCENARIOS as PROMETHEUS_SCENARIOS
from app.sources.sentry.connect import connect_sentry, probe_sentry
from app.sources.sentry.synthetic import SCENARIOS as SENTRY_SCENARIOS
from app.sources.wazuh.connect import connect_wazuh, probe_wazuh
from app.sources.wazuh.synthetic import SCENARIOS as WAZUH_SCENARIOS
from app.storage.projects import SqliteProjectRepository

DRAFT_PROJECT_ID = "draft"
DRAFT_PROJECT_NAME = "Connection test"
PREVIEW_TIMEOUT_SECONDS = 30
"""End-to-end bound of one custom-rule preview (open, query, close)."""

_SCENARIOS: dict[SourceKind, tuple[str, Collection[str]]] = {
    SourceKind.PROMETHEUS: ("url", PROMETHEUS_SCENARIOS),
    SourceKind.CLOUDFLARE: ("api_url", CLOUDFLARE_SCENARIOS),
    SourceKind.SENTRY: ("api_url", SENTRY_SCENARIOS),
    SourceKind.WAZUH: ("api_url", WAZUH_SCENARIOS),
    SourceKind.GRAFANA: ("api_url", GRAFANA_SCENARIOS),
}
"""Per kind: the input field holding the source URL, and its synthetic scenarios."""


class SourceNotConfigured(Exception):
    """The project has no data source."""


class ScenarioUnknown(Exception):
    """A ``synthetic://`` URL names no scenario of its kind; ``field`` is relative to the source."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def check_scenario(source: SourceInput) -> None:
    """Raise ScenarioUnknown when the source is synthetic with an unknown scenario."""
    field, scenarios = _SCENARIOS[source.kind]
    scenario = synthetic_url(getattr(source, field))
    if scenario is not None and scenario not in scenarios:
        raise ScenarioUnknown(
            field, f"unknown synthetic scenario; expected one of {sorted(scenarios)}"
        )


def draft_scope(matchers: list[LabelMatcher]) -> Scope:
    return Scope(project_id=DRAFT_PROJECT_ID, project_name=DRAFT_PROJECT_NAME, matchers=matchers)


@dataclass(frozen=True)
class _Opened:
    source: MetricsSource
    probe: Callable[[Scope], Awaitable[ConnectionTest]]


class SourceConnections:
    """Opens, probes and previews project sources; the analysis ``SourceProvider``.

    ``transport`` replaces the HTTP transport of every real client (tests). ``override``
    replaces every project's sources in analyses with one Prometheus source (tests and
    fixtures inject fakes this way); connection tests and previews still open real or
    synthetic sources.
    """

    def __init__(
        self,
        projects: SqliteProjectRepository,
        transport: httpx.AsyncBaseTransport | None = None,
        override: MetricsSource | None = None,
    ) -> None:
        self.projects = projects
        self.transport = transport
        self.override = override

    @asynccontextmanager
    async def open(self, scope: Scope) -> AsyncGenerator[list[OpenedSource]]:
        """Every stored source of the project. Raises SourceNotConfigured, SecretsUnreadable."""
        if self.override is not None:
            yield [OpenedSource(SourceKind.PROMETHEUS, self.override)]
            return

        connections = await self.projects.connections(scope.project_id)
        if not connections:
            raise SourceNotConfigured(scope.project_id)
        async with AsyncExitStack() as stack:
            yield [
                OpenedSource(
                    conn.kind, (await stack.enter_async_context(self._connect(conn))).source
                )
                for conn in connections
            ]

    async def test_draft(self, source: SourceInput, matchers: list[LabelMatcher]) -> ConnectionTest:
        """Connection test of an unsaved source.

        Raises ScenarioUnknown, CredentialInvalid and SecretsUnreadable.
        """
        check_scenario(source)
        conn = await self.projects.resolve_connection(source)
        return await self._probe(conn, draft_scope(matchers))

    async def health(self, project: Project) -> list[ConnectionTest]:
        """Connection test of each stored source, in order, probed concurrently.

        Raises SecretsUnreadable.
        """
        connections = await self.projects.connections(project.project_id)
        scope = Scope(
            project_id=project.project_id, project_name=project.name, matchers=project.matchers
        )
        async with asyncio.TaskGroup() as group:
            probes = [group.create_task(self._probe(conn, scope)) for conn in connections]
        return [probe.result() for probe in probes]

    async def preview_rule(
        self,
        source: PrometheusSourceInput,
        rule: CustomRule,
        matchers: list[LabelMatcher],
        end_time: datetime,
    ) -> CustomRulePreview:
        """One custom rule over the 24 h before ``end_time``; query errors and a timeout are
        in the result.

        Raises ScenarioUnknown, CredentialInvalid and SecretsUnreadable.
        """
        check_scenario(source)
        conn = await self.projects.resolve_connection(source)
        assert isinstance(conn, PrometheusConnection)
        try:
            async with asyncio.timeout(PREVIEW_TIMEOUT_SECONDS):
                async with connect_prometheus(conn, self.transport) as prometheus:
                    return await prometheus.preview_rule(
                        rule, draft_scope(matchers), end_time, STEP_SECONDS
                    )
        except TimeoutError:
            return failed_preview(
                None,
                end_time - timedelta(days=1),
                STEP_SECONDS,
                f"No answer within {PREVIEW_TIMEOUT_SECONDS} s.",
            )

    async def _probe(self, conn: SourceConnection, scope: Scope) -> ConnectionTest:
        """Connection test bounded end to end: opening and closing the source included."""
        checked_at = probe_time()
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                async with self._connect(conn) as opened:
                    return await opened.probe(scope)
        except TimeoutError:
            return timed_out(conn.kind, checked_at)

    @asynccontextmanager
    async def _connect(self, conn: SourceConnection) -> AsyncGenerator[_Opened]:
        """The open source of a connection with its probe; closed when the context exits."""
        transport = self.transport
        match conn:
            case PrometheusConnection():
                async with connect_prometheus(conn, transport) as prometheus:
                    yield _Opened(prometheus, lambda scope: probe_prometheus(prometheus, scope))
            case CloudflareConnection():
                async with connect_cloudflare(conn, transport) as cloudflare:
                    yield _Opened(cloudflare, lambda scope: probe_cloudflare(cloudflare, scope))
            case SentryConnection():
                async with connect_sentry(conn, transport) as sentry:
                    yield _Opened(sentry, lambda scope: probe_sentry(sentry, scope))
            case WazuhConnection():
                async with connect_wazuh(conn, transport) as wazuh:
                    yield _Opened(wazuh, lambda scope: probe_wazuh(wazuh, scope))
            case GrafanaConnection():
                async with connect_grafana(conn, transport) as grafana:
                    yield _Opened(grafana, lambda scope: probe_grafana(grafana, scope))
