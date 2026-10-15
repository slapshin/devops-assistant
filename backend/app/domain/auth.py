"""Source authentication through credentials (T021).

Secrets live only in credentials: a credential belongs to one source kind and holds one of
that kind's secret mechanisms (``CREDENTIAL_AUTH_TYPES``), so a kind gains a mechanism (a
custom header, a client certificate, ...) without affecting the others. A source either needs
no authentication (``none``) or references a credential of its kind (``credential``). Secrets
are write-only: input models accept them, read models only say whether one is stored.
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
"""Secret mechanisms a credential of each kind can hold."""


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
    """Use a credential of the source's kind; its secret is resolved at each use."""

    type: Literal[AuthType.CREDENTIAL] = AuthType.CREDENTIAL
    credential_id: str = Field(min_length=1, max_length=36)


SourceAuthInput = Annotated[NoAuth | CredentialRef, Field(discriminator="type")]
"""A source's authentication: none, or a credential of the source's kind."""
SecretAuthInput = Annotated[
    BearerAuthInput | BasicAuthInput | TokenAuthInput, Field(discriminator="type")
]
"""A secret mechanism of any kind (a credential's auth)."""


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
    name: str = Field(description="The credential's name.")


SourceAuth = Annotated[NoAuth | CredentialAuth, Field(discriminator="type")]
SecretAuth = Annotated[BearerAuth | BasicAuth | TokenAuth, Field(discriminator="type")]
