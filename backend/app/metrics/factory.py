"""Builds the sources of a project from its stored connection settings (T012, T014).

Every analysis or probe opens its own clients and closes them afterwards, so editing or deleting
a project can never pull a client out from under a running job.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from app.domain.common import Scope, SourceKind
from app.domain.interfaces import MetricsSource, OpenedSource
from app.domain.projects import (
    CloudflareConnection,
    PrometheusConnection,
    SourceConnection,
    synthetic_url,
)
from app.metrics.client import PrometheusClient
from app.metrics.source import PrometheusMetricsSource
from app.metrics.synthetic import SyntheticMetricsSource
from app.sources.cloudflare.connect import connect_cloudflare
from app.storage.projects import SqliteProjectRepository


class SourceNotConfigured(Exception):
    """The project has no data source."""


@asynccontextmanager
async def connect(
    conn: PrometheusConnection,
) -> AsyncIterator[tuple[MetricsSource, PrometheusClient | None]]:
    """The source for a connection, plus its HTTP client (None for synthetic sources)."""
    if (scenario := synthetic_url(conn.url)) is not None:
        yield SyntheticMetricsSource(scenario), None
        return

    client = PrometheusClient.from_connection(conn)
    try:
        yield PrometheusMetricsSource(client), client
    finally:
        await client.aclose()


@asynccontextmanager
async def open_source(conn: SourceConnection) -> AsyncIterator[MetricsSource]:
    """The MetricsSource of any connection kind."""
    match conn:
        case PrometheusConnection():
            async with connect(conn) as (prometheus, _):
                yield prometheus
        case CloudflareConnection():
            async with connect_cloudflare(conn) as cloudflare:
                yield cloudflare


class ProjectSources:
    """SourceProvider backed by the project repository.

    ``override`` replaces every project's sources with one Prometheus source (tests and
    fixtures inject fakes this way).
    """

    def __init__(
        self, projects: SqliteProjectRepository, override: MetricsSource | None = None
    ) -> None:
        self.projects = projects
        self.override = override

    @asynccontextmanager
    async def open(self, scope: Scope) -> AsyncIterator[list[OpenedSource]]:
        if self.override is not None:
            yield [OpenedSource(SourceKind.PROMETHEUS, self.override)]
            return

        connections = await self.projects.connections(scope.project_id)
        if not connections:
            raise SourceNotConfigured(scope.project_id)
        async with AsyncExitStack() as stack:
            yield [
                OpenedSource(conn.kind, await stack.enter_async_context(open_source(conn)))
                for conn in connections
            ]
