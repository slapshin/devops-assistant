"""Source authentication: the mechanisms each source kind accepts (T021).

Every source kind has its own union of mechanisms, so a kind gains a mechanism (a custom
header, a client certificate, ...) without affecting the others. A source either carries its
mechanism inline or references a shared credential of the same kind (``type: credential``);
a credential holds one of its kind's secret mechanisms (``CREDENTIAL_AUTH_TYPES``). Secrets are
write-only: input models accept them, read models only say whether one is stored.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr

from app.domain.common import Contract, SourceKind


class AuthType(StrEnum):
    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"
    TOKEN = "token"
    CREDENTIAL = "credential"


CREDENTIAL_AUTH_TYPES: dict[SourceKind, tuple[AuthType, ...]] = {
    SourceKind.PROMETHEUS: (AuthType.BEARER, AuthType.BASIC),
    SourceKind.CLOUDFLARE: (AuthType.TOKEN,),
    SourceKind.SENTRY: (AuthType.TOKEN,),
    SourceKind.WAZUH: (AuthType.BASIC,),
    SourceKind.GRAFANA: (AuthType.TOKEN,),
}
"""Secret mechanisms of each kind: what an inline secret or a shared credential can hold."""


class NoAuth(Contract):
    type: Literal[AuthType.NONE] = AuthType.NONE


# --- write models -----------------------------------------------------------------------------


class BearerAuthInput(Contract):
    """``Authorization: Bearer <token>``."""

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


class TokenAuthInput(Contract):
    """The kind's API token: Cloudflare API token, Sentry auth token, Grafana service account
    token."""

    type: Literal[AuthType.TOKEN] = AuthType.TOKEN
    token: SecretStr | None = Field(
        default=None, description="Omit to keep the stored token (update only)."
    )


class CredentialRef(Contract):
    """Use a shared credential of the source's kind; its secret is resolved at each use."""

    type: Literal[AuthType.CREDENTIAL] = AuthType.CREDENTIAL
    credential_id: str = Field(min_length=1, max_length=36)


PrometheusAuthInput = Annotated[
    NoAuth | BearerAuthInput | BasicAuthInput | CredentialRef, Field(discriminator="type")
]
CloudflareAuthInput = Annotated[
    NoAuth | TokenAuthInput | CredentialRef, Field(discriminator="type")
]
SentryAuthInput = Annotated[NoAuth | TokenAuthInput | CredentialRef, Field(discriminator="type")]
WazuhAuthInput = Annotated[NoAuth | BasicAuthInput | CredentialRef, Field(discriminator="type")]
GrafanaAuthInput = Annotated[NoAuth | TokenAuthInput | CredentialRef, Field(discriminator="type")]
SecretAuthInput = Annotated[
    BearerAuthInput | BasicAuthInput | TokenAuthInput, Field(discriminator="type")
]
"""A secret mechanism of any kind (a shared credential's auth)."""
AnyAuthInput = NoAuth | BearerAuthInput | BasicAuthInput | TokenAuthInput | CredentialRef


# --- read models ------------------------------------------------------------------------------


class BearerAuth(Contract):
    type: Literal[AuthType.BEARER] = AuthType.BEARER
    token_set: bool


class BasicAuth(Contract):
    type: Literal[AuthType.BASIC] = AuthType.BASIC
    username: str
    password_set: bool


class TokenAuth(Contract):
    type: Literal[AuthType.TOKEN] = AuthType.TOKEN
    token_set: bool


class CredentialAuth(Contract):
    type: Literal[AuthType.CREDENTIAL] = AuthType.CREDENTIAL
    credential_id: str
    name: str = Field(description="The shared credential's name.")


PrometheusAuth = Annotated[
    NoAuth | BearerAuth | BasicAuth | CredentialAuth, Field(discriminator="type")
]
CloudflareAuth = Annotated[NoAuth | TokenAuth | CredentialAuth, Field(discriminator="type")]
SentryAuth = Annotated[NoAuth | TokenAuth | CredentialAuth, Field(discriminator="type")]
WazuhAuth = Annotated[NoAuth | BasicAuth | CredentialAuth, Field(discriminator="type")]
GrafanaAuth = Annotated[NoAuth | TokenAuth | CredentialAuth, Field(discriminator="type")]
SecretAuth = Annotated[BearerAuth | BasicAuth | TokenAuth, Field(discriminator="type")]
