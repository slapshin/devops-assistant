"""Shared primitives for versioned contracts.

Domain modules must not import web framework or LLM SDK types; see docs/contracts.md.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

REPORT_SCHEMA_VERSION: Final = "2.1"
STEP_SECONDS = 300


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """RFC 3339 UTC with a trailing Z and second precision."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_to_step(moment: datetime) -> datetime:
    """Floor to the step grid so equal requests within one step share a job."""
    return datetime.fromtimestamp(int(moment.timestamp()) // STEP_SECONDS * STEP_SECONDS, UTC)


UtcDatetime = Annotated[
    AwareDatetime,
    AfterValidator(_to_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]
"""Timezone-aware timestamp normalised to UTC; naive datetimes are rejected."""

LabelValue = Annotated[str, Field(min_length=1, max_length=256)]
Labels = dict[str, str]
Ratio = Annotated[float, Field(ge=0.0, le=1.0)]


class Contract(BaseModel):
    """Base for all shared contracts: immutable, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
RESERVED_LABEL_PREFIX = "__"
MAX_MATCHERS = 10


class LabelMatcher(Contract):
    """Exact ``name="value"`` matcher added to every selector of a project's queries."""

    name: str = Field(min_length=1, max_length=128)
    value: LabelValue

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not LABEL_NAME.match(value):
            raise ValueError("must match [a-zA-Z_][a-zA-Z0-9_]*")
        if value.startswith(RESERVED_LABEL_PREFIX):
            raise ValueError("labels starting with '__' are reserved")
        return value


def normalise_matchers(matchers: list[LabelMatcher]) -> list[LabelMatcher]:
    """Reject duplicate label names and sort by name, so equal scopes render identically."""
    names = [m.name for m in matchers]
    if duplicates := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"duplicate label names: {', '.join(duplicates)}")
    return sorted(matchers, key=lambda m: m.name)


Matchers = Annotated[
    list[LabelMatcher],
    Field(min_length=1, max_length=MAX_MATCHERS),
    AfterValidator(normalise_matchers),
]
OptionalMatchers = Annotated[
    list[LabelMatcher],
    Field(max_length=MAX_MATCHERS),
    AfterValidator(normalise_matchers),
]
"""Matchers of a project; empty when it has no source that selects series by labels."""


class SourceKind(StrEnum):
    PROMETHEUS = "prometheus"
    CLOUDFLARE = "cloudflare"
    SENTRY = "sentry"


class Scope(Contract):
    """One project: every Prometheus query carries all of its matchers; reports stay inside it.

    ``matchers`` is empty only for projects without a Prometheus source; Prometheus queries
    refuse an empty scope.
    """

    project_id: str
    project_name: str
    matchers: OptionalMatchers

    @property
    def label_names(self) -> list[str]:
        return [m.name for m in self.matchers]


class TimeRange(Contract):
    """Half-open interval [start, end)."""

    start: UtcDatetime
    end: UtcDatetime


class SignalFamily(StrEnum):
    CPU = "cpu"
    MEMORY = "memory"
    FILESYSTEM = "filesystem"
    DISK_IO = "disk_io"
    NETWORK = "network"
    CONTAINER = "container"
    REQUEST_TRAFFIC = "request_traffic"
    REQUEST_FAILURES = "request_failures"
    CLIENT_ERRORS = "client_errors"
    LATENCY = "latency"
    PROXY = "proxy"
    """Reverse-proxy connections and upstream health (nginx, Angie, Caddy, Traefik)."""
    DATABASE = "database"
    """Database server health and workload (PostgreSQL via postgres_exporter, MySQL via
    mysqld_exporter, Redis via redis_exporter)."""
    EDGE = "edge"
    """CDN edge HTTP traffic: requests, 5xx/4xx, origin errors, cache hits, TTFB (Cloudflare)."""
    SECURITY = "security"
    """WAF/firewall events: blocked and challenged requests (Cloudflare)."""
    APP_ERRORS = "app_errors"
    """Application errors reported to Sentry: error events, unhandled errors, affected users."""
    APP_PERFORMANCE = "app_performance"
    """Application transactions traced by Sentry: throughput, failure rate, duration."""


_EDGE_FAMILIES = (SignalFamily.EDGE, SignalFamily.SECURITY)
_APP_FAMILIES = (SignalFamily.APP_ERRORS, SignalFamily.APP_PERFORMANCE)
SOURCE_FAMILIES: Final[dict[SourceKind, tuple[SignalFamily, ...]]] = {
    SourceKind.PROMETHEUS: tuple(
        f for f in SignalFamily if f not in _EDGE_FAMILIES + _APP_FAMILIES
    ),
    SourceKind.CLOUDFLARE: _EDGE_FAMILIES,
    SourceKind.SENTRY: _APP_FAMILIES,
}
"""Families each source kind can provide; a failed source reports these as source errors."""


class Unit(StrEnum):
    RATIO = "ratio"
    """Dimensionless 0..1 (utilisation, error ratio). UI renders as percent."""
    BYTES = "bytes"
    BYTES_PER_SECOND = "bytes_per_second"
    REQUESTS_PER_SECOND = "requests_per_second"
    PER_SECOND = "per_second"
    SECONDS = "seconds"
    COUNT = "count"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EntityKind(StrEnum):
    NODE = "node"
    FILESYSTEM = "filesystem"
    DISK = "disk"
    NETWORK_INTERFACE = "network_interface"
    CONTAINER = "container"
    SERVICE = "service"
    ROUTE = "route"
    PROXY = "proxy"
    """A reverse proxy instance, server zone, server, or service (nginx/Angie/Caddy/Traefik)."""
    UPSTREAM = "upstream"
    """A backend server behind a reverse proxy."""
    DATABASE = "database"
    """A database server (exporter target) or one database on it (PostgreSQL only)."""
    ZONE = "zone"
    """A Cloudflare zone, optionally narrowed to some of its hostnames."""
    APPLICATION = "application"
    """A Sentry project, optionally narrowed to one environment."""


class Entity(Contract):
    """Resource identity built from actual labels only; never inferred across namespaces."""

    kind: EntityKind
    key: str = Field(
        min_length=1,
        description="Stable canonical key, e.g. 'node|job=node|instance=shop-production'.",
    )
    display_name: str
    labels: Labels = Field(description="Identity labels only (no scope matchers).")


class Reason(Contract):
    """Machine code plus human message, used for confidence, coverage, and exclusions."""

    code: str
    message: str
