"""Shared primitives for versioned contracts.

Domain modules must not import web framework or LLM SDK types; see docs/contracts.md.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

REPORT_SCHEMA_VERSION: Final = "1.0"
STEP_SECONDS = 300


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """RFC 3339 UTC with a trailing Z and second precision."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


class Scope(Contract):
    """Exactly one project/env pair. Every query and report stays inside it."""

    project: LabelValue
    env: LabelValue


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


class Entity(Contract):
    """Resource identity built from actual labels only; never inferred across namespaces."""

    kind: EntityKind
    key: str = Field(
        min_length=1,
        description="Stable canonical key, e.g. 'node|job=node|instance=paas-production'.",
    )
    display_name: str
    labels: Labels = Field(description="Identity labels only (no project/env).")


class Reason(Contract):
    """Machine code plus human message, used for confidence, coverage, and exclusions."""

    code: str
    message: str
