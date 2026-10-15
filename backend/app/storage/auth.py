"""Stored auth mechanisms, shared by source configs and credentials (T021).

A mechanism is stored as public JSON (``{"type": ..., "username": ...}``) plus a secrets dict
that is encrypted separately: ``{"token": ...}`` or ``{"password": ...}``.
"""

from collections.abc import Callable
from typing import Any

from app.domain.auth import (
    AuthType,
    BasicAuth,
    BasicAuthInput,
    BearerAuth,
    BearerAuthInput,
    NoAuth,
    TokenAuth,
    TokenAuthInput,
)
from app.domain.common import SourceKind

LEGACY_TOKEN_KEYS = ("api_token", "auth_token")
"""Cloudflare and Sentry tokens saved before T021; re-keyed to ``token`` on the next save."""


class SecretRequired(Exception):
    """A secret was omitted but nothing of that kind is stored to keep."""

    def __init__(self, field: str) -> None:
        super().__init__(f"{field} is required")
        self.field = field


class CredentialInvalid(Exception):
    """A source references a credential that is missing or belongs to another kind."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def secret_value(secrets: dict[str, str], field: str) -> str | None:
    if field == "token":
        return next((secrets[k] for k in ("token", *LEGACY_TOKEN_KEYS) if k in secrets), None)
    return secrets.get(field)


def split_secret(
    auth: BearerAuthInput | BasicAuthInput | TokenAuthInput,
    stored: dict[str, Any] | None,
    stored_secrets: Callable[[], dict[str, str]] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Public JSON and secrets of ``auth``; an omitted secret is kept from a stored mechanism
    of the same type. Raises SecretRequired with the field path relative to the source."""
    public: dict[str, Any] = {"type": auth.type.value}
    match auth:
        case BasicAuthInput(username=username, password=secret):
            public["username"] = username
            field = "password"
        case BearerAuthInput(token=secret) | TokenAuthInput(token=secret):
            field = "token"
    if secret is not None:
        return public, {field: secret.get_secret_value()}
    if stored is None or stored.get("type") != auth.type.value or stored_secrets is None:
        raise SecretRequired(f"auth.{field}")
    kept = secret_value(stored_secrets(), field)
    if kept is None:
        raise SecretRequired(f"auth.{field}")
    return public, {field: kept}


def secret_view(
    public: dict[str, Any], has_secret: bool
) -> NoAuth | BearerAuth | BasicAuth | TokenAuth:
    match AuthType(public["type"]):
        case AuthType.BEARER:
            return BearerAuth(token_set=has_secret)
        case AuthType.BASIC:
            return BasicAuth(username=public["username"], password_set=has_secret)
        case AuthType.TOKEN:
            return TokenAuth(token_set=has_secret)
        case _:
            return NoAuth()


def connection_auth(
    kind: SourceKind, public: dict[str, Any], secrets: dict[str, str]
) -> dict[str, Any]:
    """The auth fields of ``kind``'s connection model for a stored mechanism."""
    auth_type = AuthType(public["type"])
    token, password = secret_value(secrets, "token"), secret_value(secrets, "password")
    username = public.get("username")
    match kind, auth_type:
        case SourceKind.PROMETHEUS, AuthType.BEARER:
            return {"bearer_token": token}
        case SourceKind.PROMETHEUS, AuthType.BASIC:
            return {"basic_auth_user": username, "basic_auth_password": password}
        case SourceKind.CLOUDFLARE, AuthType.TOKEN:
            return {"api_token": token}
        case SourceKind.SENTRY, AuthType.TOKEN:
            return {"auth_token": token}
        case SourceKind.WAZUH, AuthType.BASIC:
            return {"username": username, "password": password}
        case SourceKind.GRAFANA, AuthType.TOKEN:
            return {"token": token}
        case _:
            return {}
