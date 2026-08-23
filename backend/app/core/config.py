from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnvironment = Literal["development", "test", "production"]
LLMProviderName = Literal[
    "mock",
    "openai",
    "gemini",
    "groq",
    "openrouter",
    "ollama",
]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    app_name: str = "AI SQL Data Analyst"
    app_env: AppEnvironment = "development"
    app_debug: bool = False

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_base_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:8501"

    frontend_host: str = "127.0.0.1"
    frontend_port: int = Field(default=8501, ge=1, le=65535)

    database_url: str | None = None
    analytics_database_url: str | None = None

    llm_provider: LLMProviderName = "mock"
    llm_model: str = "deterministic-test"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=1200, ge=1, le=32768)
    llm_request_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)

    query_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    statement_timeout_ms: int = Field(default=8000, ge=100, le=300000)
    max_result_rows: int = Field(default=1000, ge=1, le=10000)
    max_sql_repair_attempts: int = Field(default=2, ge=0, le=5)
    max_question_length: int = Field(default=2000, ge=1, le=10000)

    schema_cache_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    conversation_history_limit: int = Field(default=10, ge=0, le=100)

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
    audit_log_enabled: bool = True
    audit_retention_days: int = Field(default=30, ge=1, le=3650)
    metrics_enabled: bool = True

    @field_validator("app_env", "llm_provider", "log_format", mode="before")
    @classmethod
    def normalize_lowercase(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def cors_origins(self) -> tuple[str, ...]:
        return tuple(
            origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()
        )

    @model_validator(mode="after")
    def validate_security_configuration(self) -> Self:
        if not self.cors_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must contain at least one origin")

        if self.app_env == "production" and self.app_debug:
            raise ValueError("APP_DEBUG cannot be enabled in production")

        external_providers = {"openai", "gemini", "groq", "openrouter"}
        api_key = self.llm_api_key.get_secret_value()

        if self.llm_provider in external_providers and not api_key:
            raise ValueError(f"LLM_API_KEY is required when LLM_PROVIDER={self.llm_provider}")

        if self.llm_provider == "ollama" and not self.llm_base_url:
            raise ValueError("LLM_BASE_URL is required when LLM_PROVIDER=ollama")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
