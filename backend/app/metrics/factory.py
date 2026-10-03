"""Builds the MetricsSource of a project from its stored connection settings (T012).

Every analysis or probe opens its own client and closes it afterwards, so editing or deleting
a project can never pull a client out from under a running job.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from app.domain.common import Scope
from app.domain.interfaces import MetricsSource
from app.domain.projects import PrometheusConnection
from app.metrics.client import PrometheusClient
from app.metrics.source import PrometheusMetricsSource
from app.metrics.synthetic import SyntheticMetricsSource
from app.storage.projects import SqliteProjectRepository


class SourceNotConfigured(Exception):
    """The project has no metrics source."""


def synthetic_scenario(url: str) -> str | None:
    parts = urlsplit(url)
    return parts.netloc if parts.scheme == "synthetic" else None


@asynccontextmanager
async def connect(
    conn: PrometheusConnection,
) -> AsyncIterator[tuple[MetricsSource, PrometheusClient | None]]:
    """The source for a connection, plus its HTTP client (None for synthetic sources)."""
    if (scenario := synthetic_scenario(conn.url)) is not None:
        yield SyntheticMetricsSource(scenario), None
        return

    client = PrometheusClient.from_connection(conn)
    try:
        yield PrometheusMetricsSource(client), client
    finally:
        await client.aclose()


class ProjectSources:
    """SourceProvider backed by the project repository.

    ``override`` replaces every project's source (tests and fixtures inject fakes this way).
    """

    def __init__(
        self, projects: SqliteProjectRepository, override: MetricsSource | None = None
    ) -> None:
        self.projects = projects
        self.override = override

    @asynccontextmanager
    async def open(self, scope: Scope) -> AsyncIterator[MetricsSource]:
        if self.override is not None:
            yield self.override
            return

        conn = await self.projects.prometheus_connection(scope.project_id)
        if conn is None:
            raise SourceNotConfigured(scope.project_id)
        async with connect(conn) as (source, _):
            yield source
