"""Projects: user-managed analysis scopes and the sources they are analysed from (T011).

A project selects its series with equality label matchers and holds one configuration per
source kind. Secrets are write-only: input models accept them, read models only say whether
one is stored. New source kinds (Wazuh, Cloudflare, Sentry, ...) are added as new members of
``SourceKind`` and of the source unions without changing storage.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator

from app.domain.common import (
    Contract,
    LabelMatcher,
    Matchers,
    SignalFamily,
    UtcDatetime,
)
from app.domain.jobs import AnalysisJob
from app.domain.metrics import CapabilityStatus
from app.domain.schedule import ReportSchedule

MAX_PROJECT_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 1000


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


class SourceKind(StrEnum):
    PROMETHEUS = "prometheus"


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


SourceInput = PrometheusSourceInput
"""Becomes a discriminated union on ``kind`` once a second source kind exists."""


class ProjectInput(Contract):
    """Body of POST /api/projects and PUT /api/projects/{project_id}."""

    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME_CHARS)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    matchers: Matchers
    sources: list[SourceInput] = Field(default_factory=list, max_length=len(SourceKind))
    schedule: ReportSchedule | None = Field(
        default=None, description="Automatic analyses; null runs analyses on demand only."
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

    @model_validator(mode="after")
    def _one_source_per_kind(self) -> Self:
        kinds = [s.kind for s in self.sources]
        if len(kinds) != len(set(kinds)):
            raise ValueError("at most one source per kind")
        return self


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


Source = PrometheusSource


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
    next_scheduled_run: UtcDatetime | None = Field(
        default=None, description="Next automatic analysis; null without a schedule."
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime

    def source(self, kind: SourceKind) -> Source | None:
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
    matchers: Matchers
    source: SourceInput


class FamilyCapability(Contract):
    family: SignalFamily
    status: CapabilityStatus
    reason: str | None = None


class ConnectionTest(Contract):
    reachable: bool
    auth_ok: bool | None = Field(description="Null when the source could not be reached.")
    matched_series: int | None = Field(
        default=None, description="Series currently matching all matchers."
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
