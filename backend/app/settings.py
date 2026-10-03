"""Environment configuration (docs/DECISIONS.md §2) with actionable validation errors."""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.explanation import ExplanationStatus
from app.domain.projects import PrometheusConnection, validate_source_url


class AIProvider(StrEnum):
    OPENAI = "openai"
    NONE = "none"
    FAKE = "fake"
    """Deterministic template provider for tests and offline demos (no network)."""


class ConfigError(Exception):
    """Startup configuration error listing each invalid variable."""


CONFIG_ENV_FILE = Path(__file__).resolve().parents[2] / "config.env"
"""Repository-root config.env (copied from config.env.template); ignored when absent."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=CONFIG_ENV_FILE, extra="ignore", frozen=True)

    # Deprecated (T012): sources are configured per project. Read only once, to give projects
    # migrated from pre-project reports the source those reports were produced from.
    metrics_url: str | None = None
    metrics_bearer_token: SecretStr | None = None
    metrics_basic_auth_user: str | None = None
    metrics_basic_auth_password: SecretStr | None = None
    metrics_tls_verify: bool = True

    ai_provider: AIProvider = AIProvider.OPENAI
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_base_url: str | None = None

    data_dir: Path = Path("./data")
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    detector_config_file: Path | None = None
    ui_static_dir: Path | None = None
    """Built web UI to serve at /. Defaults to ../frontend/dist when it exists."""
    log_level: str = "INFO"
    secret_key: SecretStr | None = None
    """Fernet key for stored project secrets. Defaults to DATA_DIR/secret.key (generated)."""

    demo_projects: bool = False
    """Seed one synthetic demo project per scenario when no project exists."""

    @field_validator("metrics_url", mode="before")
    @classmethod
    def _valid_metrics_url(cls, value: str | None) -> str | None:
        return validate_source_url(value) if value else None

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("expected DEBUG, INFO, WARNING, or ERROR")
        return upper

    @field_validator("detector_config_file")
    @classmethod
    def _detector_config_exists(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_file():
            raise ValueError(f"file not found: {value}")
        return value

    @model_validator(mode="after")
    def _auth_exclusive(self) -> Self:
        basic = self.metrics_basic_auth_user is not None or self.metrics_basic_auth_password
        if basic and self.metrics_bearer_token is not None:
            raise ValueError("METRICS_BEARER_TOKEN and METRICS_BASIC_AUTH_* are mutually exclusive")
        if (self.metrics_basic_auth_user is None) != (self.metrics_basic_auth_password is None):
            raise ValueError(
                "METRICS_BASIC_AUTH_USER and METRICS_BASIC_AUTH_PASSWORD must be set together"
            )
        return self

    @property
    def metrics_connection(self) -> PrometheusConnection | None:
        if self.metrics_url is None:
            return None
        return PrometheusConnection(
            url=self.metrics_url,
            tls_verify=self.metrics_tls_verify,
            bearer_token=self.metrics_bearer_token,
            basic_auth_user=self.metrics_basic_auth_user,
            basic_auth_password=self.metrics_basic_auth_password,
        )

    @property
    def explanation_status(self) -> ExplanationStatus:
        """Static AI availability; PENDING means configured (per-report status varies)."""
        if self.ai_provider is AIProvider.NONE:
            return ExplanationStatus.DISABLED
        if self.ai_provider is AIProvider.FAKE:
            return ExplanationStatus.PENDING
        if self.openai_api_key is None or not self.openai_model:
            return ExplanationStatus.NOT_CONFIGURED
        return ExplanationStatus.PENDING

    @property
    def explanation_hint(self) -> str | None:
        if self.explanation_status is not ExplanationStatus.NOT_CONFIGURED:
            return None
        missing = [
            name
            for name, value in (
                ("OPENAI_API_KEY", self.openai_api_key),
                ("OPENAI_MODEL", self.openai_model),
            )
            if not value
        ]
        return f"Set {' and '.join(missing)} or AI_PROVIDER=none"

    @property
    def ui_dir(self) -> Path | None:
        if self.ui_static_dir is not None:
            return self.ui_static_dir if (self.ui_static_dir / "index.html").is_file() else None
        default = Path(__file__).resolve().parents[2] / "frontend" / "dist"
        return default if (default / "index.html").is_file() else None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "assistant.sqlite3"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting validation failures into one actionable ConfigError."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = "_".join(str(p) for p in err["loc"]).upper() or "SETTINGS"
            value = err.get("input")
            shown = "<hidden>" if any(s in loc for s in ("TOKEN", "PASSWORD", "KEY")) else value
            message = err["msg"].removeprefix("Value error, ")
            lines.append(f"  {loc}: {message}" + (f" (got {shown!r})" if loc != "SETTINGS" else ""))
        raise ConfigError("Invalid configuration:\n" + "\n".join(lines)) from None
