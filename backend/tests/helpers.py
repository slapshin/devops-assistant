"""Shared test helpers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.domain.common import LabelMatcher, Scope
from app.domain.interfaces import MetricsSource


def make_scope(project: str = "paas", env: str = "production", project_id: str = "p-1") -> Scope:
    """Scope of a project selecting ``project=<project>, env=<env>`` (the pre-T012 shape)."""
    return Scope(
        project_id=project_id,
        project_name=f"{project} / {env}",
        matchers=[LabelMatcher(name="project", value=project), LabelMatcher(name="env", value=env)],
    )


class StaticSources:
    """SourceProvider that serves one source for every project."""

    def __init__(self, source: MetricsSource) -> None:
        self.source = source

    @asynccontextmanager
    async def open(self, scope: Scope) -> AsyncIterator[MetricsSource]:
        yield self.source
