"""Projects: user-managed analysis scopes and the sources they are analysed from (T011, T014).

A project holds at most one data source per kind; its equality label matchers select the
series of its Prometheus source. Secrets are write-only: input models accept them, read models
only say whether one is stored. New source kinds (Wazuh, Sentry, ...) are added as new members
of ``SourceKind`` and of the source unions without changing storage.
"""

import re
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Discriminator, Field, SecretStr, Tag, ValidationInfo, field_validator

from app.domain.common import (
    Contract,
    LabelMatcher,
    OptionalMatchers,
    SignalFamily,
    SourceKind,
    UtcDatetime,
)
from app.domain.jobs import AnalysisJob
from app.domain.metrics import CapabilityStatus
from app.domain.schedule import ReportSchedule

MAX_PROJECT_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 1000
MAX_KEEP_REPORTS = 1000
CLOUDFLARE_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
MAX_HOSTNAMES = 20
_ZONE_ID = re.compile(r"^[0-9a-f]{32}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
)


def synthetic_url(url: str) -> str | None:
    """The scenario of a synthetic://<scenario> URL, else None."""
    parts = urlsplit(url)
    return parts.netloc if parts.scheme == "synthetic" else None


def validate_source_url(value: str) -> str:
    """http(s) URL without credentials, query, or fragment; or synthetic://<scenario>."""
    parts = urlsplit(value)
    if parts.scheme == "synthetic":
        if not parts.netloc:
            raise ValueError("expected synthetic://<scenario>, e.g. synthetic://incident")
        return value
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("expected an http(s) URL such as http://localhost:8428")
    if parts.username or parts.password:
        raise ValueError("must not contain credentials; configure authentication separately")
    if parts.query or parts.fragment:
        raise ValueError("must not contain a query string or fragment")
    return value.rstrip("/")


class AuthType(StrEnum):
    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"


class NoAuth(Contract):
    type: Literal[AuthType.NONE] = AuthType.NONE


# --- write models -----------------------------------------------------------------------------


class BearerAuthInput(Contract):
    type: Literal[AuthType.BEARER] = AuthType.BEARER
    token: SecretStr | None = Field(
        default=None, description="Omit to keep the stored token (update only)."
    )


class BasicAuthInput(Contract):
    type: Literal[AuthType.BASIC] = AuthType.BASIC
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr | None = Field(
        default=None, description="Omit to keep the stored password (update only)."
    )


AuthInput = Annotated[NoAuth | BearerAuthInput | BasicAuthInput, Field(discriminator="type")]


class PrometheusSourceInput(Contract):
    kind: Literal[SourceKind.PROMETHEUS] = SourceKind.PROMETHEUS
    url: str = Field(max_length=2048)
    tls_verify: bool = True
    auth: AuthInput = Field(default_factory=NoAuth)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return validate_source_url(value)


class CloudflareSourceInput(Contract):
    """One Cloudflare zone, optionally narrowed to some of its hostnames."""

    kind: Literal[SourceKind.CLOUDFLARE] = SourceKind.CLOUDFLARE
    zone_id: str = Field(description="Zone ID (32 hex characters, dashboard → Overview).")
    hostnames: list[str] = Field(
        default_factory=list,
        max_length=MAX_HOSTNAMES,
        description="Analyse only these hostnames; empty analyses the whole zone.",
    )
    api_token: SecretStr | None = Field(
        default=None,
        description="API token with Analytics:Read on the zone. Omit to keep the stored one "
        "(update only); not needed for synthetic://.",
    )
    api_url: str = Field(
        default=CLOUDFLARE_GRAPHQL_URL,
        max_length=2048,
        description="GraphQL endpoint; synthetic://<scenario> for demo data.",
    )

    @field_validator("zone_id")
    @classmethod
    def _valid_zone_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not _ZONE_ID.match(value):
            raise ValueError("expected a zone ID of 32 hex characters")
        return value

    @field_validator("hostnames")
    @classmethod
    def _valid_hostnames(cls, value: list[str]) -> list[str]:
        names = sorted({h.strip().lower().rstrip(".") for h in value})
        if bad := [h for h in names if not _HOSTNAME.match(h)]:
            raise ValueError(f"invalid hostnames: {', '.join(bad)}")
        return names

    @field_validator("api_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return validate_source_url(value)


def _source_kind(value: Any) -> str:
    """``kind`` selects the union member; it defaults to prometheus (the only pre-T015 kind)."""
    if isinstance(value, dict):
        return str(value.get("kind", SourceKind.PROMETHEUS.value))
    return str(getattr(value, "kind", SourceKind.PROMETHEUS.value))


SourceInput = Annotated[
    Annotated[PrometheusSourceInput, Tag(SourceKind.PROMETHEUS.value)]
    | Annotated[CloudflareSourceInput, Tag(SourceKind.CLOUDFLARE.value)],
    Discriminator(_source_kind),
]


class ProjectInput(Contract):
    """Body of POST /api/projects and PUT /api/projects/{project_id}."""

    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME_CHARS)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    sources: list[SourceInput] = Field(default_factory=list, max_length=len(SourceKind))
    matchers: OptionalMatchers = Field(
        default_factory=list,
        validate_default=True,
        description="Required (1-10) with a Prometheus source.",
    )
    schedule: ReportSchedule | None = Field(
        default=None, description="Automatic analyses; null runs analyses on demand only."
    )
    keep_reports: int | None = Field(
        default=None,
        ge=1,
        le=MAX_KEEP_REPORTS,
        description="Keep only this many newest reports; older analyses are deleted "
        "automatically. Null keeps all.",
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not (stripped := value.strip()):
            raise ValueError("must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("sources")
    @classmethod
    def _one_source_per_kind(cls, value: list[SourceInput]) -> list[SourceInput]:
        kinds = [s.kind for s in value]
        if len(kinds) != len(set(kinds)):
            raise ValueError("at most one source per kind")
        return value

    @field_validator("matchers")
    @classmethod
    def _matchers_for_prometheus(
        cls, value: list[LabelMatcher], info: ValidationInfo
    ) -> list[LabelMatcher]:
        sources: list[SourceInput] = info.data.get("sources", [])
        return require_matchers(value, [s.kind for s in sources])


def require_matchers(matchers: list[LabelMatcher], kinds: list[SourceKind]) -> list[LabelMatcher]:
    """Prometheus queries are scoped by the matchers, so that source needs at least one."""
    if SourceKind.PROMETHEUS in kinds and not matchers:
        raise ValueError("a Prometheus source needs at least one label matcher")
    return matchers


# --- read models ------------------------------------------------------------------------------


class BearerAuth(Contract):
    type: Literal[AuthType.BEARER] = AuthType.BEARER
    token_set: bool


class BasicAuth(Contract):
    type: Literal[AuthType.BASIC] = AuthType.BASIC
    username: str
    password_set: bool


Auth = Annotated[NoAuth | BearerAuth | BasicAuth, Field(discriminator="type")]


class PrometheusSource(Contract):
    kind: Literal[SourceKind.PROMETHEUS] = SourceKind.PROMETHEUS
    url: str
    tls_verify: bool
    auth: Auth


class CloudflareSource(Contract):
    kind: Literal[SourceKind.CLOUDFLARE] = SourceKind.CLOUDFLARE
    zone_id: str
    hostnames: list[str]
    api_url: str
    token_set: bool


Source = Annotated[PrometheusSource | CloudflareSource, Field(discriminator="kind")]


class Project(Contract):
    project_id: str
    name: str
    description: str | None = None
    matchers: list[LabelMatcher]
    sources: list[Source]
    credentials_readable: bool = Field(
        default=True,
        description="False when stored secrets cannot be decrypted (lost or changed key).",
    )
    schedule: ReportSchedule | None = None
    keep_reports: int | None = Field(
        default=None, description="Newest reports kept; null keeps all."
    )
    next_scheduled_run: UtcDatetime | None = Field(
        default=None, description="Next automatic analysis; null without a schedule."
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime

    def source(self, kind: SourceKind) -> PrometheusSource | CloudflareSource | None:
        return next((s for s in self.sources if s.kind is kind), None)


class ProjectSummary(Project):
    """A project with its analysis activity (list and detail pages)."""

    latest_analysis: AnalysisJob | None = Field(default=None, description="Newest finished job.")
    active_analysis: AnalysisJob | None = Field(default=None, description="Queued or running job.")
    report_count: int = Field(default=0, ge=0, description="Saved reports (deleted with it).")


class ProjectList(Contract):
    items: list[ProjectSummary]


# --- connection test --------------------------------------------------------------------------


class ConnectionTestRequest(Contract):
    """Body of POST /api/projects/test-connection: a draft, possibly reusing stored secrets."""

    project_id: str | None = Field(
        default=None, description="Reuse this project's stored secrets for omitted ones."
    )
    source: SourceInput
    matchers: OptionalMatchers = Field(
        default_factory=list,
        validate_default=True,
        description="Required with a Prometheus source.",
    )

    @field_validator("matchers")
    @classmethod
    def _matchers_for_prometheus(
        cls, value: list[LabelMatcher], info: ValidationInfo
    ) -> list[LabelMatcher]:
        source: SourceInput | None = info.data.get("source")
        return require_matchers(value, [source.kind] if source else [])


class FamilyCapability(Contract):
    family: SignalFamily
    status: CapabilityStatus
    reason: str | None = None


class ConnectionTest(Contract):
    kind: SourceKind = SourceKind.PROMETHEUS
    reachable: bool
    auth_ok: bool | None = Field(description="Null when the source could not be reached.")
    matched_series: int | None = Field(
        default=None,
        description="Prometheus: series currently matching all matchers.",
    )
    history_days: float | None = Field(
        default=None, description="Days of matching history found, up to 30."
    )
    families: list[FamilyCapability] = Field(default_factory=list)
    message: str | None = None
    checked_at: UtcDatetime


class PrometheusConnection(Contract):
    """Resolved connection settings including decrypted secrets; never serialised to clients."""

    url: str
    tls_verify: bool = True
    bearer_token: SecretStr | None = None
    basic_auth_user: str | None = None
    basic_auth_password: SecretStr | None = None

    @property
    def kind(self) -> SourceKind:
        return SourceKind.PROMETHEUS


class CloudflareConnection(Contract):
    """Resolved Cloudflare settings including the decrypted token; never serialised to clients."""

    zone_id: str
    hostnames: list[str] = Field(default_factory=list)
    api_url: str = CLOUDFLARE_GRAPHQL_URL
    api_token: SecretStr | None = None

    @property
    def kind(self) -> SourceKind:
        return SourceKind.CLOUDFLARE


SourceConnection = PrometheusConnection | CloudflareConnection
"""Decrypted connection of any source kind."""
