"""Shared test helpers."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.domain.common import LabelMatcher, Scope, SourceKind
from app.domain.interfaces import MetricsSource, OpenedSource


def make_scope(project: str = "shop", env: str = "production", project_id: str = "p-1") -> Scope:
    """Scope of a project selecting ``project=<project>, env=<env>`` (the pre-T012 shape)."""
    return Scope(
        project_id=project_id,
        project_name=f"{project} / {env}",
        matchers=[LabelMatcher(name="project", value=project), LabelMatcher(name="env", value=env)],
    )


class StaticSources:
    """SourceProvider that serves the same sources (Prometheus by default) for every project."""

    def __init__(self, *sources: MetricsSource | OpenedSource) -> None:
        self.sources = [
            s if isinstance(s, OpenedSource) else OpenedSource(SourceKind.PROMETHEUS, s)
            for s in sources
        ]

    @asynccontextmanager
    async def open(self, scope: Scope) -> AsyncGenerator[list[OpenedSource]]:
        yield self.sources
