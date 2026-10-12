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
    ValidationInfo,
    field_validator,
)

REPORT_SCHEMA_VERSION: Final = "2.2"
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


class MatchOp(StrEnum):
    """PromQL label matching operator; regular expressions are fully anchored (RE2 syntax)."""

    EQUAL = "="
    NOT_EQUAL = "!="
    REGEX = "=~"
    NOT_REGEX = "!~"


class LabelMatcher(Contract):
    """``name<op>"value"`` matcher, e.g. ``env="prod"`` or ``env=~"prod|staging"``.

    An absent label matches as the empty string, as in PromQL: ``env!="dev"`` matches series
    without ``env``.
    """

    name: str = Field(min_length=1, max_length=128)
    op: MatchOp = MatchOp.EQUAL
    value: LabelValue

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not LABEL_NAME.match(value):
            raise ValueError("must match [a-zA-Z_][a-zA-Z0-9_]*")
        if value.startswith(RESERVED_LABEL_PREFIX):
            raise ValueError("labels starting with '__' are reserved")
        return value

    @field_validator("value")
    @classmethod
    def _valid_regex(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("op") in (MatchOp.REGEX, MatchOp.NOT_REGEX):
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        return value

    def matches(self, value: str | None) -> bool:
        """Whether a label value (``None`` when absent) satisfies this matcher."""
        actual = value or ""
        match self.op:
            case MatchOp.EQUAL:
                return actual == self.value
            case MatchOp.NOT_EQUAL:
                return actual != self.value
            case MatchOp.REGEX:
                return re.fullmatch(self.value, actual) is not None
            case MatchOp.NOT_REGEX:
                return re.fullmatch(self.value, actual) is None

    @property
    def restricts(self) -> bool:
        """Selects only series that carry the label, so it narrows a selector on its own."""
        return self.op in (MatchOp.EQUAL, MatchOp.REGEX) and not self.matches("")

    def __str__(self) -> str:
        return f'{self.name}{self.op}"{self.value}"'


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
    WAZUH = "wazuh"
    GRAFANA = "grafana"


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
    HOST_SECURITY = "host_security"
    """Wazuh HIDS alerts of an agent: all alerts, high-level alerts, authentication failures."""
    FILE_INTEGRITY = "file_integrity"
    """Wazuh file integrity monitoring (syscheck): files added, modified, or deleted."""
    ALERTING = "alerting"
    """Grafana alert rules defined by the team: firing instances, rules that cannot evaluate."""


_EDGE_FAMILIES = (SignalFamily.EDGE, SignalFamily.SECURITY)
_APP_FAMILIES = (SignalFamily.APP_ERRORS, SignalFamily.APP_PERFORMANCE)
_HOST_SECURITY_FAMILIES = (SignalFamily.HOST_SECURITY, SignalFamily.FILE_INTEGRITY)
_ALERTING_FAMILIES = (SignalFamily.ALERTING,)
SOURCE_FAMILIES: Final[dict[SourceKind, tuple[SignalFamily, ...]]] = {
    SourceKind.PROMETHEUS: tuple(
        f
        for f in SignalFamily
        if f not in _EDGE_FAMILIES + _APP_FAMILIES + _HOST_SECURITY_FAMILIES + _ALERTING_FAMILIES
    ),
    SourceKind.CLOUDFLARE: _EDGE_FAMILIES,
    SourceKind.SENTRY: _APP_FAMILIES,
    SourceKind.WAZUH: _HOST_SECURITY_FAMILIES,
    SourceKind.GRAFANA: _ALERTING_FAMILIES,
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
    AGENT = "agent"
    """A Wazuh agent (``agent.name``); never matched to Prometheus nodes."""
    ALERT_RULE = "alert_rule"
    """A Grafana-managed alert rule (by UID); its instances are counted, not matched to hosts."""


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
