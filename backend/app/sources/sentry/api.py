"""What the Sentry source needs from the API, as typed results.

``SentryClient`` implements it over the REST API; ``SyntheticSentryApi`` generates demo data.
Values are per 5-minute bucket (the bucket's start in epoch seconds), durations in seconds.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.sources.sentry.catalog import Query

SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class ProjectInfo:
    """The analysed Sentry project (``GET /api/0/projects/{org}/{project}/``)."""

    id: str
    """Numeric project ID; ``events-timeseries`` filters by it (older versions reject slugs)."""
    slug: str
    name: str
    created: datetime | None = None
    """When the project was created; nothing can be reported before it."""


@dataclass
class Chunk:
    """Values of one query over [start, end): signal -> bucket start -> value."""

    query: str
    values: dict[str, dict[int, float]] = field(default_factory=dict)


class SentryApi(Protocol):
    transactions_dataset: str
    """Dataset of the transactions query (``spans`` or ``transactions``), chosen by probing."""

    @property
    def base_url(self) -> str: ...

    @property
    def backend(self) -> str | None: ...

    def begin(self, end_time: datetime) -> None:
        """Called once per analysis or probe with its frozen end time."""
        ...

    async def projects(self) -> list[ProjectInfo]:
        """The configured projects, in slug order; raises SourceError(AUTH) when one is
        missing or not readable."""
        ...

    async def series(self, query: Query, project_ids: Sequence[str], start: int, end: int) -> Chunk:
        """The signals of ``query`` per 5-minute bucket of [start, end), summed over the
        projects (numeric IDs from ``projects``)."""
        ...

    async def aclose(self) -> None: ...
