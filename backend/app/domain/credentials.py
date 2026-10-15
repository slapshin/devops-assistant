"""Credentials: the secrets sources authenticate with, usable by many projects (T021).

A credential belongs to one source kind and holds one of that kind's secret mechanisms; sources
of that kind reference it with ``auth: {type: credential, credential_id}``. Rotating it updates
every referencing source at once. A credential in use cannot be deleted or change its kind.
"""

from pydantic import Field, ValidationInfo, field_validator

from app.domain.auth import CREDENTIAL_AUTH_TYPES, SecretAuth, SecretAuthInput
from app.domain.common import Contract, SourceKind, UtcDatetime

MAX_CREDENTIAL_NAME_CHARS = 100
MAX_CREDENTIAL_DESCRIPTION_CHARS = 1000


class CredentialInput(Contract):
    """Body of POST /api/credentials and PUT /api/credentials/{credential_id}."""

    name: str = Field(min_length=1, max_length=MAX_CREDENTIAL_NAME_CHARS)
    description: str | None = Field(default=None, max_length=MAX_CREDENTIAL_DESCRIPTION_CHARS)
    kind: SourceKind = Field(description="Only sources of this kind can use the credential.")
    auth: SecretAuthInput = Field(
        description="Prometheus: bearer or basic; Wazuh: basic; Cloudflare, Sentry and "
        "Grafana: token. Omit the secret on update to keep the stored one."
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

    @field_validator("auth")
    @classmethod
    def _kind_accepts(cls, value: SecretAuthInput, info: ValidationInfo) -> SecretAuthInput:
        kind: SourceKind | None = info.data.get("kind")
        if kind is not None and value.type not in CREDENTIAL_AUTH_TYPES[kind]:
            allowed = ", ".join(CREDENTIAL_AUTH_TYPES[kind])
            raise ValueError(f"a {kind} credential holds {allowed}")
        return value


class CredentialUsage(Contract):
    project_id: str
    name: str


class Credential(Contract):
    credential_id: str
    name: str
    description: str | None = None
    kind: SourceKind
    auth: SecretAuth
    readable: bool = Field(
        default=True,
        description="False when the stored secret cannot be decrypted (lost or changed key).",
    )
    used_by: list[CredentialUsage] = Field(
        default_factory=list, description="Projects whose sources use the credential."
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CredentialList(Contract):
    items: list[Credential]
