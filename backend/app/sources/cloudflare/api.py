"""What the Cloudflare source needs from the API, as typed results.

``CloudflareClient`` implements it over GraphQL; ``SyntheticCloudflareApi`` generates demo data.
Counts are per 5-minute bucket (the bucket's start in epoch seconds), durations in seconds.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

HTTP_DATASET = "httpRequestsAdaptiveGroups"
FIREWALL_DATASET = "firewallEventsAdaptiveGroups"
DATASETS = (HTTP_DATASET, FIREWALL_DATASET)

SECONDS_PER_DAY = 86400
DEFAULT_MAX_DURATION = SECONDS_PER_DAY
DEFAULT_NOT_OLDER_THAN = 31 * SECONDS_PER_DAY
"""Cloudflare: adaptive datasets retain at least 31 days on every plan."""
DEFAULT_PAGE_SIZE = 10000


@dataclass(frozen=True)
class DatasetSettings:
    """Per-zone query limits of one dataset (GraphQL ``settings`` node)."""

    enabled: bool = True
    max_duration: int = DEFAULT_MAX_DURATION
    """Widest time range of one query, in seconds."""
    not_older_than: int = DEFAULT_NOT_OLDER_THAN
    """How far back a query can read, in seconds."""
    max_page_size: int = DEFAULT_PAGE_SIZE
    verified: bool = True
    """False when the settings could not be read and these are documented defaults."""


@dataclass
class Chunk:
    """Values of one query over [start, end): signal -> bucket start -> value."""

    query: str
    values: dict[str, dict[int, float]] = field(default_factory=dict)
    sample_intervals: dict[int, float] = field(default_factory=dict)
    """Mean sampling interval per bucket (1 = every request counted)."""
    truncated: bool = False
    """A result hit the page-size limit, so buckets may be missing."""


class CloudflareApi(Protocol):
    @property
    def base_url(self) -> str: ...

    @property
    def backend(self) -> str | None: ...

    def begin(self, end_time: datetime) -> None:
        """Called once per analysis or probe with its frozen end time."""
        ...

    async def settings(self) -> dict[str, DatasetSettings]: ...

    async def zone_name(self) -> str | None:
        """The zone's domain, or None when the token cannot read it (needs Zone:Read)."""
        ...

    async def traffic(self, start: int, end: int, page_size: int) -> Chunk:
        """Requests, 5xx, origin errors (520-530), 404, 4xx and cache hits per bucket."""
        ...

    async def timing(self, start: int, end: int, page_size: int) -> Chunk:
        """Edge TTFB p95/p99 and origin response time p95 per bucket (Pro plan and up)."""
        ...

    async def security(self, start: int, end: int, page_size: int) -> Chunk:
        """Blocked and challenged firewall events per bucket."""
        ...

    async def daily_requests(self, first: date, last: date) -> dict[date, float]:
        """Requests per day of the whole zone (history probe)."""
        ...

    async def aclose(self) -> None: ...
