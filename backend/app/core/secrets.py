"""Centralized secret management for GCP Secret Manager (Etapa 3.4).

In production, Cloud Run mounts secrets from Secret Manager as environment
variables natively — no Python library required. This module provides
validation, documentation, and mapping of expected secrets.

Secret rotation: update the secret value in GCP Secret Manager, then restart
the Cloud Run service. The new value is picked up on next cold start.
"""

from dataclasses import dataclass

from backend.app.core.config import Settings


@dataclass(frozen=True, slots=True)
class SecretMapping:
    """Maps a Secret Manager secret name to its environment variable."""

    secret_name: str
    env_var: str
    required: bool


SECRET_MAPPINGS: tuple[SecretMapping, ...] = (
    SecretMapping("llm-api-key", "LLM_API_KEY", required=True),
    SecretMapping("database-url", "DATABASE_URL", required=True),
    SecretMapping("analytics-database-url", "ANALYTICS_DATABASE_URL", required=True),
    SecretMapping("cloud-sql-password", "CLOUD_SQL_PASSWORD", required=False),
)


def get_secret_mappings() -> tuple[SecretMapping, ...]:
    """Return the list of secrets managed by Secret Manager."""
    return SECRET_MAPPINGS


def validate_secrets(settings: Settings) -> list[str]:
    """Validate that required secrets are configured when Secret Manager is enabled.

    Returns a list of missing required secret environment variable names.
    An empty list means all required secrets are present.
    """
    if not settings.secret_manager_enabled:
        return []

    import os

    missing: list[str] = []
    for mapping in SECRET_MAPPINGS:
        if mapping.required:
            value = os.environ.get(mapping.env_var, "")
            if not value:
                missing.append(mapping.env_var)
    return missing


def get_secret_names() -> tuple[str, ...]:
    """Return the names of all secrets in Secret Manager."""
    return tuple(m.secret_name for m in SECRET_MAPPINGS)
