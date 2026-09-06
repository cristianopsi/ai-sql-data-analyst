"""Tests for centralized secret management (Etapa 3.4)."""

from backend.app.core.config import Settings
from backend.app.core.secrets import (
    SecretMapping,
    get_secret_mappings,
    get_secret_names,
    validate_secrets,
)


class TestSecretMapping:
    """Test SecretMapping dataclass."""

    def test_secret_mapping_is_frozen(self) -> None:
        mapping = SecretMapping("test-secret", "TEST_ENV", required=True)
        assert mapping.secret_name == "test-secret"
        assert mapping.env_var == "TEST_ENV"
        assert mapping.required is True

    def test_secret_mapping_optional(self) -> None:
        mapping = SecretMapping("opt-secret", "OPT_ENV", required=False)
        assert mapping.required is False


class TestSecretMappings:
    """Test the SECRET_MAPPINGS registry."""

    def test_get_secret_mappings_returns_tuple(self) -> None:
        mappings = get_secret_mappings()
        assert isinstance(mappings, tuple)
        assert len(mappings) == 4

    def test_get_secret_mappings_has_llm_api_key(self) -> None:
        mappings = get_secret_mappings()
        llm = [m for m in mappings if m.secret_name == "llm-api-key"]
        assert len(llm) == 1
        assert llm[0].env_var == "LLM_API_KEY"
        assert llm[0].required is True

    def test_get_secret_mappings_has_database_url(self) -> None:
        mappings = get_secret_mappings()
        db = [m for m in mappings if m.secret_name == "database-url"]
        assert len(db) == 1
        assert db[0].env_var == "DATABASE_URL"
        assert db[0].required is True

    def test_get_secret_mappings_has_analytics_database_url(self) -> None:
        mappings = get_secret_mappings()
        analytics = [m for m in mappings if m.secret_name == "analytics-database-url"]
        assert len(analytics) == 1
        assert analytics[0].env_var == "ANALYTICS_DATABASE_URL"
        assert analytics[0].required is True

    def test_get_secret_mappings_has_cloud_sql_password_optional(self) -> None:
        mappings = get_secret_mappings()
        pwd = [m for m in mappings if m.secret_name == "cloud-sql-password"]
        assert len(pwd) == 1
        assert pwd[0].required is False

    def test_get_secret_names(self) -> None:
        names = get_secret_names()
        assert "llm-api-key" in names
        assert "database-url" in names
        assert "analytics-database-url" in names
        assert "cloud-sql-password" in names
        assert len(names) == 4


class TestValidateSecrets:
    """Test validate_secrets function."""

    def test_validate_returns_empty_when_disabled(self) -> None:
        settings = Settings(secret_manager_enabled=False)
        missing = validate_secrets(settings)
        assert missing == []

    def test_validate_returns_empty_when_disabled_regardless_of_env(
        self, monkeypatch: object
    ) -> None:
        import os

        # Clear all secret env vars
        for var in ("LLM_API_KEY", "DATABASE_URL", "ANALYTICS_DATABASE_URL"):
            monkeypatch.delenv(var, raising=False)  # type: ignore[attr-defined]

        settings = Settings(secret_manager_enabled=False)
        missing = validate_secrets(settings)
        assert missing == []

    def test_validate_returns_missing_when_enabled_and_no_env(self, monkeypatch: object) -> None:
        for var in ("LLM_API_KEY", "DATABASE_URL", "ANALYTICS_DATABASE_URL"):
            monkeypatch.delenv(var, raising=False)  # type: ignore[attr-defined]

        settings = Settings(secret_manager_enabled=True)
        missing = validate_secrets(settings)
        assert "LLM_API_KEY" in missing
        assert "DATABASE_URL" in missing
        assert "ANALYTICS_DATABASE_URL" in missing

    def test_validate_returns_empty_when_enabled_and_all_present(self, monkeypatch: object) -> None:
        monkeypatch.setenv("LLM_API_KEY", "test-key")  # type: ignore[attr-defined]
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")  # type: ignore[attr-defined]
        monkeypatch.setenv(  # type: ignore[attr-defined]
            "ANALYTICS_DATABASE_URL", "postgresql://user:pass@host:5432/db"
        )

        settings = Settings(secret_manager_enabled=True)
        missing = validate_secrets(settings)
        assert missing == []

    def test_validate_ignores_optional_secrets(self, monkeypatch: object) -> None:
        monkeypatch.setenv("LLM_API_KEY", "test-key")  # type: ignore[attr-defined]
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")  # type: ignore[attr-defined]
        monkeypatch.setenv(  # type: ignore[attr-defined]
            "ANALYTICS_DATABASE_URL", "postgresql://user:pass@host:5432/db"
        )
        monkeypatch.delenv("CLOUD_SQL_PASSWORD", raising=False)  # type: ignore[attr-defined]

        settings = Settings(secret_manager_enabled=True)
        missing = validate_secrets(settings)
        assert missing == []

    def test_validate_partial_missing(self, monkeypatch: object) -> None:
        monkeypatch.setenv("LLM_API_KEY", "test-key")  # type: ignore[attr-defined]
        monkeypatch.delenv("DATABASE_URL", raising=False)  # type: ignore[attr-defined]
        monkeypatch.delenv("ANALYTICS_DATABASE_URL", raising=False)  # type: ignore[attr-defined]

        settings = Settings(secret_manager_enabled=True)
        missing = validate_secrets(settings)
        assert "LLM_API_KEY" not in missing
        assert "DATABASE_URL" in missing
        assert "ANALYTICS_DATABASE_URL" in missing


class TestSecretManagerConfig:
    """Test Secret Manager configuration fields."""

    def test_secret_manager_disabled_by_default(self) -> None:
        settings = Settings()
        assert settings.secret_manager_enabled is False

    def test_secret_manager_can_be_enabled(self) -> None:
        settings = Settings(secret_manager_enabled=True)
        assert settings.secret_manager_enabled is True

    def test_secret_manager_independent_from_cloud_sql(self) -> None:
        settings = Settings(
            secret_manager_enabled=True,
            cloud_sql_connector_enabled=False,
        )
        assert settings.secret_manager_enabled is True
        assert settings.cloud_sql_connector_enabled is False
