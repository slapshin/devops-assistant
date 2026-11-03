"""Keep developer settings (the Makefile exports config.env) out of the tests."""

import pytest

ISOLATED_ENV = (
    "METRICS_URL",
    "METRICS_BEARER_TOKEN",
    "METRICS_BASIC_AUTH_USER",
    "METRICS_BASIC_AUTH_PASSWORD",
    "METRICS_TLS_VERIFY",
    "SECRET_KEY",
    "DEMO_PROJECTS",
    "EMAIL_PROVIDER",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_TLS_VERIFY",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "APP_PUBLIC_URL",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
