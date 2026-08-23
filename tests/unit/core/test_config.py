import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings, get_settings


def test_default_settings_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.app_debug is False
    assert settings.llm_provider == "mock"
    assert settings.llm_api_key.get_secret_value() == ""
    assert settings.cors_origins == ("http://localhost:8501",)
    assert settings.max_result_rows == 1000
    assert settings.max_sql_repair_attempts == 2


def test_values_are_normalized() -> None:
    settings = Settings(
        app_env="TEST",
        llm_provider="MOCK",
        log_format="CONSOLE",
        log_level="warning",
        cors_allowed_origins="http://localhost:8501, http://127.0.0.1:8501",
        _env_file=None,
    )

    assert settings.app_env == "test"
    assert settings.llm_provider == "mock"
    assert settings.log_format == "console"
    assert settings.log_level == "WARNING"
    assert settings.cors_origins == (
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    )


def test_debug_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="APP_DEBUG cannot be enabled"):
        Settings(
            app_env="production",
            app_debug=True,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "provider",
    ["openai", "gemini", "groq", "openrouter"],
)
def test_external_provider_requires_api_key(provider: str) -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        Settings(
            llm_provider=provider,
            llm_api_key="",
            _env_file=None,
        )


def test_external_provider_accepts_configured_api_key() -> None:
    settings = Settings(
        llm_provider="openai",
        llm_api_key="test-key-not-a-real-secret",
        _env_file=None,
    )

    assert settings.llm_provider == "openai"
    assert settings.llm_api_key.get_secret_value() == "test-key-not-a-real-secret"
    assert "test-key-not-a-real-secret" not in repr(settings)


def test_ollama_requires_base_url() -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL is required"):
        Settings(
            llm_provider="ollama",
            llm_base_url="",
            _env_file=None,
        )


def test_empty_cors_origins_are_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="CORS_ALLOWED_ORIGINS must contain at least one origin",
    ):
        Settings(
            cors_allowed_origins=" , ",
            _env_file=None,
        )


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.app_env == "test"

    get_settings.cache_clear()


def test_database_pool_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_pool_min_size == 1
    assert settings.database_pool_max_size == 5
    assert settings.database_pool_timeout_seconds == 5.0
    assert settings.database_connect_timeout_seconds == 5


def test_database_pool_minimum_cannot_exceed_maximum() -> None:
    with pytest.raises(
        ValidationError,
        match=("DATABASE_POOL_MIN_SIZE cannot exceed DATABASE_POOL_MAX_SIZE"),
    ):
        Settings(
            database_pool_min_size=6,
            database_pool_max_size=5,
            _env_file=None,
        )
