"""Tests for network security configuration (Etapa 3.3)."""

from backend.app.core.config import Settings


class TestCloudSQLConfig:
    """Test Cloud SQL configuration fields."""

    def test_cloud_sql_connector_disabled_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_connector_enabled is False

    def test_cloud_sql_app_user_empty_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_app_user == ""

    def test_cloud_sql_analytics_user_empty_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_analytics_user == ""

    def test_cloud_sql_instance_empty_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_instance == ""

    def test_cloud_sql_database_empty_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_database == ""

    def test_cloud_sql_app_password_empty_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_app_password == ""

    def test_cloud_sql_analytics_password_empty_by_default(self) -> None:
        settings = Settings()
        assert settings.cloud_sql_analytics_password == ""

    def test_cloud_sql_enabled_when_set(self) -> None:
        settings = Settings(
            cloud_sql_connector_enabled=True,
            cloud_sql_instance="ai-sql-data-analyst-prod:southamerica-east1:retail-db",
            cloud_sql_database="retail_analytics",
            cloud_sql_app_user="ai_sql_app",
            cloud_sql_app_password="secret_app",
            cloud_sql_analytics_user="analytics",
            cloud_sql_analytics_password="secret_analytics",
        )
        assert settings.cloud_sql_connector_enabled is True
        assert "southamerica-east1" in settings.cloud_sql_instance
        assert settings.cloud_sql_app_user == "ai_sql_app"
        assert settings.cloud_sql_analytics_user == "analytics"


class TestCORSConfig:
    """Test CORS origins configuration."""

    def test_cors_origins_default_localhost(self) -> None:
        settings = Settings()
        assert "http://localhost:8501" in settings.cors_origins

    def test_cors_origins_single_value(self) -> None:
        settings = Settings(cors_allowed_origins="https://app.example.com")
        assert settings.cors_origins == ("https://app.example.com",)

    def test_cors_origins_multiple_values(self) -> None:
        settings = Settings(
            cors_allowed_origins="https://app.example.com,https://staging.example.com"
        )
        assert len(settings.cors_origins) == 2
        assert "https://app.example.com" in settings.cors_origins
        assert "https://staging.example.com" in settings.cors_origins

    def test_cors_origins_production_excludes_localhost(self) -> None:
        settings = Settings(
            cors_allowed_origins="https://app.example.com,https://admin.example.com"
        )
        assert "http://localhost:8501" not in settings.cors_origins
        assert all(not o.startswith("http://localhost") for o in settings.cors_origins)

    def test_cors_origins_no_wildcard_in_production(self) -> None:
        settings = Settings(cors_allowed_origins="https://app.example.com")
        assert "*" not in settings.cors_origins


class TestHTTPSAndSecurityHeaders:
    """Test security-related configuration for production."""

    def test_auth_enabled_is_false_by_default(self) -> None:
        settings = Settings()
        assert settings.auth_enabled is False

    def test_production_settings_have_auth_enabled(self) -> None:
        settings = Settings(
            auth_enabled=True,
            oidc_issuer="https://accounts.google.com",
            cors_allowed_origins="https://app.example.com",
        )
        assert settings.auth_enabled is True
        assert "http://localhost" not in settings.cors_allowed_origins

    def test_cloud_sql_connector_enabled_flag_is_independent(self) -> None:
        settings = Settings(cloud_sql_connector_enabled=True)
        assert settings.cloud_sql_connector_enabled is True
        assert settings.auth_enabled is False
