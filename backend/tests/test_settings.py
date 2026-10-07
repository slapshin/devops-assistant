import pytest

from app.domain.explanation import ExplanationStatus
from app.settings import ConfigError, Settings, load_settings


def settings(**kw: object) -> Settings:
    return load_settings(_env_file=None, **kw)


def test_defaults_need_no_metrics_connection_or_ai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("METRICS_URL", "AI_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)

    s = settings()

    assert s.metrics_url is None and s.metrics_connection is None
    assert s.explanation_status is ExplanationStatus.NOT_CONFIGURED
    assert s.explanation_hint == "Set OPENAI_API_KEY and OPENAI_MODEL or AI_PROVIDER=none"


def test_ai_disabled_and_configured_states() -> None:
    assert settings(ai_provider="none").explanation_status is ExplanationStatus.DISABLED

    configured = settings(openai_api_key="sk-test", openai_model="m")
    assert configured.explanation_status is ExplanationStatus.PENDING


def test_path_prefix_is_preserved() -> None:
    s = settings(metrics_url="https://vm.example:8481/select/0/prometheus/")
    assert s.metrics_url == "https://vm.example:8481/select/0/prometheus"


def test_credentials_in_url_are_rejected_with_actionable_message() -> None:
    with pytest.raises(ConfigError) as exc:
        settings(metrics_url="http://user:pw@localhost:8428")

    assert "METRICS_URL" in str(exc.value)
    assert "must not contain credentials" in str(exc.value)


def test_invalid_url_names_variable_and_expected_form() -> None:
    with pytest.raises(ConfigError, match=r"METRICS_URL: expected an http\(s\) URL"):
        settings(metrics_url="localhost:8428")


def test_auth_methods_are_exclusive_and_secrets_hidden() -> None:
    with pytest.raises(ConfigError, match="mutually exclusive") as exc:
        settings(
            metrics_bearer_token="tok-secret",
            metrics_basic_auth_user="u",
            metrics_basic_auth_password="p-secret",
        )
    assert "tok-secret" not in str(exc.value) and "p-secret" not in str(exc.value)


def test_basic_auth_requires_both_parts() -> None:
    with pytest.raises(ConfigError, match="must be set together"):
        settings(metrics_basic_auth_user="u")


def test_invalid_port_is_reported() -> None:
    with pytest.raises(ConfigError, match="APP_PORT"):
        settings(app_port=70000)
