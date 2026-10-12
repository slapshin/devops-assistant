"""Projects: user-managed analysis scopes and the sources they are analysed from (T011, T014).

A project holds at most one data source per kind; its equality label matchers select the
series of its Prometheus source. Secrets are write-only: input models accept them, read models
only say whether one is stored. New source kinds are added as new members
of ``SourceKind`` and of the source unions without changing storage.
"""

import re
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    Discriminator,
    Field,
    SecretStr,
    Tag,
    ValidationInfo,
    field_validator,
    model_validator,
)

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
SENTRY_URL = "https://sentry.io"
_SENTRY_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_SENTRY_ENVIRONMENT = re.compile(r"^[^\s/]{1,64}$")
_SENTRY_TAG_KEY = re.compile(r"^[a-zA-Z0-9_.:-]{1,32}$")
MAX_SENTRY_PROJECTS = 10
MAX_SENTRY_TAGS = 10
MAX_SENTRY_TAG_VALUE_CHARS = 200
WAZUH_INDEX_PATTERN = "wazuh-alerts-4.x-*"
_WAZUH_AGENT = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_WAZUH_LABEL_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_WAZUH_INDEX_PATTERN = re.compile(r"^[a-z0-9_*][a-z0-9_.*+-]{0,254}$")
MAX_WAZUH_AGENTS = 50
MAX_WAZUH_LABELS = 10
MAX_WAZUH_LABEL_VALUE_CHARS = 200
WAZUH_MONITORING_INDEX_PATTERN = "wazuh-monitoring-*"
_WAZUH_GROUP = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
MAX_WAZUH_GROUPS = 10
_GRAFANA_UID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_GRAFANA_FOLDERS = 20
MAX_GRAFANA_RULES = 100
MAX_GRAFANA_FOLDER_CHARS = 255
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
        description="API token with Analytics:Read on the zone (and optionally Zone:Read, to "
        "show its domain). Omit to keep the stored one (update only); not needed for "
        "synthetic://.",
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


class SentryTag(Contract):
    """Exact ``key:"value"`` tag filter added to every Sentry query of the source."""

    key: str = Field(description="Tag key, e.g. server_name, release or a custom tag.")
    value: str = Field(min_length=1, max_length=MAX_SENTRY_TAG_VALUE_CHARS)

    @field_validator("key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        value = value.strip()
        if not _SENTRY_TAG_KEY.match(value):
            raise ValueError("expected letters, digits, _ . : - (max 32)")
        return value

    @field_validator("value")
    @classmethod
    def _valid_value(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("must not contain line breaks")
        return value


def normalise_sentry_tags(tags: list[SentryTag]) -> list[SentryTag]:
    """Reject duplicate keys (two values of one key never match) and sort by key."""
    keys = [t.key for t in tags]
    if duplicates := sorted({k for k in keys if keys.count(k) > 1}):
        raise ValueError(f"duplicate tag keys: {', '.join(duplicates)}")
    return sorted(tags, key=lambda t: t.key)


def normalise_sentry_projects(projects: list[str]) -> list[str]:
    """Lower-cased, unique, sorted project slugs."""
    slugs = sorted({p.strip().lower() for p in projects})
    if bad := [p for p in slugs if not _SENTRY_SLUG.match(p)]:
        raise ValueError(f"invalid project slugs: {', '.join(bad)}")
    if not slugs:
        raise ValueError("at least one project is required")
    return slugs


SentryProjects = Annotated[
    list[str],
    Field(min_length=1, max_length=MAX_SENTRY_PROJECTS),
    AfterValidator(normalise_sentry_projects),
]
SentryTags = Annotated[
    list[SentryTag], Field(max_length=MAX_SENTRY_TAGS), AfterValidator(normalise_sentry_tags)
]


class SentrySourceInput(Contract):
    """Sentry projects of one organization, optionally narrowed to an environment and tags."""

    kind: Literal[SourceKind.SENTRY] = SourceKind.SENTRY
    organization: str = Field(description="Organization slug (Settings → General).")
    projects: SentryProjects = Field(
        description="Project slugs (Settings → Projects); each is analysed as its own entity."
    )
    environment: str | None = Field(
        default=None, description="Analyse only this environment; null analyses all of them."
    )
    tags: SentryTags = Field(
        default_factory=list,
        description="Tag filters applied to every project, all of which must match.",
    )
    auth_token: SecretStr | None = Field(
        default=None,
        description="Auth token with org:read and project:read. Omit to keep the stored one "
        "(update only); not needed for synthetic://.",
    )
    api_url: str = Field(
        default=SENTRY_URL,
        max_length=2048,
        description="Sentry base URL: https://sentry.io, https://de.sentry.io for EU "
        "organizations, or a self-hosted instance (path prefixes are kept); "
        "synthetic://<scenario> for demo data.",
    )
    tls_verify: bool = Field(
        default=True,
        description="Verify the server's TLS certificate; turn off only for a self-hosted "
        "Sentry with a self-signed or internal certificate.",
    )

    @field_validator("organization")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not _SENTRY_SLUG.match(value):
            raise ValueError("expected a slug of lower-case letters, digits, - and _")
        return value

    @field_validator("environment")
    @classmethod
    def _valid_environment(cls, value: str | None) -> str | None:
        if not (value := (value or "").strip()):
            return None
        if not _SENTRY_ENVIRONMENT.match(value) or value == "None":
            raise ValueError("expected an environment name without spaces or '/' (max 64)")
        return value

    @field_validator("api_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return validate_source_url(value)


class WazuhLabel(Contract):
    """Exact ``agent.labels.<key> = value`` filter (an agent's ``<labels>`` setting)."""

    key: str = Field(description="Label key as set in the agent's <labels> block, e.g. project.")
    value: str = Field(min_length=1, max_length=MAX_WAZUH_LABEL_VALUE_CHARS)

    @field_validator("key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        value = value.strip()
        if not _WAZUH_LABEL_KEY.match(value):
            raise ValueError("expected letters, digits, _ . - (max 64)")
        return value

    @field_validator("value")
    @classmethod
    def _valid_value(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("must not contain line breaks")
        return value


def normalise_wazuh_labels(labels: list[WazuhLabel]) -> list[WazuhLabel]:
    """Reject duplicate keys (two values of one key never match) and sort by key."""
    keys = [label.key for label in labels]
    if duplicates := sorted({k for k in keys if keys.count(k) > 1}):
        raise ValueError(f"duplicate label keys: {', '.join(duplicates)}")
    return sorted(labels, key=lambda label: label.key)


def normalise_wazuh_agents(agents: list[str]) -> list[str]:
    """Unique, sorted agent names (case is kept: agent.name is matched exactly)."""
    names = sorted({a.strip() for a in agents if a.strip()})
    if bad := [a for a in names if not _WAZUH_AGENT.match(a)]:
        raise ValueError(f"invalid agent names: {', '.join(bad)}")
    return names


WazuhAgents = Annotated[
    list[str], Field(max_length=MAX_WAZUH_AGENTS), AfterValidator(normalise_wazuh_agents)
]


def normalise_wazuh_groups(groups: list[str]) -> list[str]:
    """Unique, sorted group names (case is kept, as Wazuh stores them)."""
    names = sorted({g.strip() for g in groups if g.strip()})
    if bad := [g for g in names if not _WAZUH_GROUP.match(g) or g in (".", "..")]:
        raise ValueError(f"invalid group names: {', '.join(bad)}")
    return names


WazuhGroups = Annotated[
    list[str], Field(max_length=MAX_WAZUH_GROUPS), AfterValidator(normalise_wazuh_groups)
]
WazuhLabels = Annotated[
    list[WazuhLabel], Field(max_length=MAX_WAZUH_LABELS), AfterValidator(normalise_wazuh_labels)
]


def validate_index_pattern(value: str) -> str:
    """One index name or wildcard pattern: no lists, exclusions, or remote clusters."""
    value = value.strip()
    if not _WAZUH_INDEX_PATTERN.match(value):
        raise ValueError(
            "expected one lower-case index pattern such as wazuh-alerts-4.x-* "
            "(no commas, spaces, ':' or leading '-')"
        )
    return value


class WazuhSourceInput(Contract):
    """Wazuh agents selected by name, group and/or agent labels, read from the Wazuh indexer.

    The named agents and the groups' members together form the selection; label filters
    narrow it (or alone select every agent that has those labels).
    """

    kind: Literal[SourceKind.WAZUH] = SourceKind.WAZUH
    api_url: str = Field(
        max_length=2048,
        description="Wazuh indexer URL, e.g. https://wazuh-indexer:9200 (path prefixes are "
        "kept); synthetic://<scenario> for demo data.",
    )
    index_pattern: str = Field(
        default=WAZUH_INDEX_PATTERN, description="Alerts index pattern of the indexer."
    )
    agents: WazuhAgents = Field(
        default_factory=list,
        description="Agent names (agent.name); each is analysed as its own entity.",
    )
    groups: WazuhGroups = Field(
        default_factory=list,
        description="Agent groups; their current members (from the monitoring index) are "
        "analysed together with the named agents.",
    )
    labels: WazuhLabels = Field(
        default_factory=list,
        description="Agent label filters (agent.labels.<key>), all of which must match.",
    )
    monitoring_index_pattern: str = Field(
        default=WAZUH_MONITORING_INDEX_PATTERN,
        description="Index of the agent snapshots the Wazuh dashboard writes; used to resolve "
        "groups.",
    )
    username: str | None = Field(
        default=None,
        max_length=256,
        description="Indexer user with read access to the alerts indices; not needed for "
        "synthetic://.",
    )
    password: SecretStr | None = Field(
        default=None,
        description="Omit to keep the stored password (update only); not needed for synthetic://.",
    )
    tls_verify: bool = Field(
        default=True,
        description="Verify the indexer's TLS certificate; turn off only for a self-signed or "
        "internal certificate (the Wazuh default).",
    )

    @field_validator("api_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return validate_source_url(value)

    @field_validator("index_pattern", "monitoring_index_pattern")
    @classmethod
    def _valid_index_pattern(cls, value: str) -> str:
        return validate_index_pattern(value)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _selects_agents(self) -> WazuhSourceInput:
        if not self.agents and not self.groups and not self.labels:
            raise ValueError("select agents by name, group or label (at least one is required)")
        return self


def normalise_grafana_folders(folders: list[str]) -> list[str]:
    """Unique, sorted folder UIDs or titles (matched exactly, case kept)."""
    names = sorted({f.strip() for f in folders if f.strip()})
    if bad := [f for f in names if len(f) > MAX_GRAFANA_FOLDER_CHARS or "\n" in f or "\r" in f]:
        raise ValueError(f"invalid folder names: {', '.join(bad)}")
    return names


def normalise_grafana_rules(uids: list[str]) -> list[str]:
    """Unique, sorted alert rule UIDs."""
    names = sorted({u.strip() for u in uids if u.strip()})
    if bad := [u for u in names if not _GRAFANA_UID.match(u)]:
        raise ValueError(f"invalid rule UIDs: {', '.join(bad)}")
    return names


GrafanaFolders = Annotated[
    list[str], Field(max_length=MAX_GRAFANA_FOLDERS), AfterValidator(normalise_grafana_folders)
]
GrafanaRules = Annotated[
    list[str], Field(max_length=MAX_GRAFANA_RULES), AfterValidator(normalise_grafana_rules)
]


class GrafanaSourceInput(Contract):
    """Grafana-managed alert rules selected by folder, UID and/or rule labels.

    The folders' rules and the listed rules together form the selection; rule label matchers
    narrow it (or alone select every rule with those labels). Instance label matchers then
    narrow which alert instances of those rules count, e.g. ``project=shop``.
    """

    kind: Literal[SourceKind.GRAFANA] = SourceKind.GRAFANA
    api_url: str = Field(
        max_length=2048,
        description="Grafana URL, e.g. https://grafana.example.com (path prefixes are kept); "
        "synthetic://<scenario> for demo data.",
    )
    org_id: int = Field(default=1, ge=1, description="Grafana organization of the rules.")
    folders: GrafanaFolders = Field(
        default_factory=list, description="Folder UIDs or titles whose alert rules are analysed."
    )
    rule_uids: GrafanaRules = Field(
        default_factory=list, description="Alert rule UIDs analysed in addition to the folders."
    )
    rule_labels: OptionalMatchers = Field(
        default_factory=list,
        description="Rule label matchers (e.g. team=web), all of which must match.",
    )
    instance_labels: OptionalMatchers = Field(
        default_factory=list,
        description="Alert instance label matchers (e.g. project=shop), all of which must "
        "match; instances without them are not counted.",
    )
    token: SecretStr | None = Field(
        default=None,
        description="Service account token (Viewer role). Omit to keep the stored one (update "
        "only); not needed for synthetic://.",
    )
    tls_verify: bool = Field(
        default=True,
        description="Verify Grafana's TLS certificate; turn off only for a self-signed or "
        "internal certificate.",
    )

    @field_validator("api_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return validate_source_url(value)

    @model_validator(mode="after")
    def _selects_rules(self) -> GrafanaSourceInput:
        if not self.folders and not self.rule_uids and not self.rule_labels:
            raise ValueError(
                "select alert rules by folder, UID or rule label (at least one is required)"
            )
        return self


def _source_kind(value: Any) -> str:
    """``kind`` selects the union member; it defaults to prometheus (the only pre-T015 kind)."""
    if isinstance(value, dict):
        return str(value.get("kind", SourceKind.PROMETHEUS.value))
    return str(getattr(value, "kind", SourceKind.PROMETHEUS.value))


SourceInput = Annotated[
    Annotated[PrometheusSourceInput, Tag(SourceKind.PROMETHEUS.value)]
    | Annotated[CloudflareSourceInput, Tag(SourceKind.CLOUDFLARE.value)]
    | Annotated[SentrySourceInput, Tag(SourceKind.SENTRY.value)]
    | Annotated[WazuhSourceInput, Tag(SourceKind.WAZUH.value)]
    | Annotated[GrafanaSourceInput, Tag(SourceKind.GRAFANA.value)],
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
    """Prometheus queries are scoped by the matchers, so that source needs at least one that
    selects only series carrying its label (``=`` or ``=~`` not matching the empty string)."""
    if SourceKind.PROMETHEUS in kinds:
        if not matchers:
            raise ValueError("a Prometheus source needs at least one label matcher")
        if not any(m.restricts for m in matchers):
            raise ValueError(
                "a Prometheus source needs at least one '=' or '=~' label matcher "
                "that does not match an empty value"
            )
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


class SentrySource(Contract):
    kind: Literal[SourceKind.SENTRY] = SourceKind.SENTRY
    organization: str
    projects: list[str]
    environment: str | None = None
    tags: list[SentryTag] = Field(default_factory=list)
    api_url: str
    tls_verify: bool = True
    token_set: bool


class WazuhSource(Contract):
    kind: Literal[SourceKind.WAZUH] = SourceKind.WAZUH
    api_url: str
    index_pattern: str = WAZUH_INDEX_PATTERN
    agents: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    labels: list[WazuhLabel] = Field(default_factory=list)
    monitoring_index_pattern: str = WAZUH_MONITORING_INDEX_PATTERN
    username: str | None = None
    tls_verify: bool = True
    password_set: bool


class GrafanaSource(Contract):
    kind: Literal[SourceKind.GRAFANA] = SourceKind.GRAFANA
    api_url: str
    org_id: int = 1
    folders: list[str] = Field(default_factory=list)
    rule_uids: list[str] = Field(default_factory=list)
    rule_labels: list[LabelMatcher] = Field(default_factory=list)
    instance_labels: list[LabelMatcher] = Field(default_factory=list)
    tls_verify: bool = True
    token_set: bool


AnySource = PrometheusSource | CloudflareSource | SentrySource | WazuhSource | GrafanaSource
Source = Annotated[AnySource, Field(discriminator="kind")]


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

    def source(self, kind: SourceKind) -> AnySource | None:
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
        description="Prometheus: series currently matching all matchers. Cloudflare: requests "
        "in the last 24 h. Sentry: error events and transactions in the last 24 h. Wazuh: "
        "alerts of the selected agents in the last 24 h. Grafana: current alert instances of the "
        "selected rules that match the instance labels.",
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


class SentryConnection(Contract):
    """Resolved Sentry settings including the decrypted token; never serialised to clients."""

    organization: str
    projects: list[str]
    environment: str | None = None
    tags: list[SentryTag] = Field(default_factory=list)
    api_url: str = SENTRY_URL
    tls_verify: bool = True
    auth_token: SecretStr | None = None

    @property
    def kind(self) -> SourceKind:
        return SourceKind.SENTRY


class WazuhConnection(Contract):
    """Resolved Wazuh settings including the decrypted password; never serialised to clients."""

    api_url: str
    index_pattern: str = WAZUH_INDEX_PATTERN
    agents: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    labels: list[WazuhLabel] = Field(default_factory=list)
    monitoring_index_pattern: str = WAZUH_MONITORING_INDEX_PATTERN
    username: str | None = None
    password: SecretStr | None = None
    tls_verify: bool = True

    @property
    def kind(self) -> SourceKind:
        return SourceKind.WAZUH


class GrafanaConnection(Contract):
    """Resolved Grafana settings including the decrypted token; never serialised to clients."""

    api_url: str
    org_id: int = 1
    folders: list[str] = Field(default_factory=list)
    rule_uids: list[str] = Field(default_factory=list)
    rule_labels: list[LabelMatcher] = Field(default_factory=list)
    instance_labels: list[LabelMatcher] = Field(default_factory=list)
    token: SecretStr | None = None
    tls_verify: bool = True

    @property
    def kind(self) -> SourceKind:
        return SourceKind.GRAFANA


SourceConnection = (
    PrometheusConnection
    | CloudflareConnection
    | SentryConnection
    | WazuhConnection
    | GrafanaConnection
)
"""Decrypted connection of any source kind."""
