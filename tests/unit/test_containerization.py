from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _compose() -> dict[str, Any]:
    value = yaml.safe_load(_read("compose.yaml"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_backend_dockerfile_uses_non_root_runtime_and_safe_healthcheck() -> None:
    source = _read("Dockerfile.backend")

    assert source.startswith("FROM python:3.13-slim-bookworm")
    assert "USER 10001:10001" in source
    assert "COPY --chown=10001:10001 . /app" in source
    assert "python -m pip install --no-cache-dir ." in source
    assert "urllib.request.urlopen" in source
    assert "/ready" in source
    assert "EXPOSE 8000" in source
    assert "curl " not in source
    assert "sudo " not in source


def test_frontend_dockerfile_uses_non_root_runtime_and_safe_healthcheck() -> None:
    source = _read("Dockerfile.frontend")

    assert source.startswith("FROM python:3.13-slim-bookworm")
    assert "USER 10001:10001" in source
    assert "COPY --chown=10001:10001 . /app" in source
    assert "python -m pip install --no-cache-dir ." in source
    assert "urllib.request.urlopen" in source
    assert "/_stcore/health" in source
    assert "EXPOSE 8501" in source
    assert "curl " not in source
    assert "sudo " not in source


def test_dockerignore_excludes_secrets_caches_tests_and_local_environment() -> None:
    exclusions = set(_read(".dockerignore").splitlines())

    assert {
        ".git",
        ".venv",
        ".env",
        ".env.*",
        "__pycache__/",
        "*.py[cod]",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        "tests/",
        "*.log",
    } <= exclusions
    assert "!.env.example" in exclusions


def test_compose_defines_only_postgres_backend_and_frontend() -> None:
    compose = _compose()

    assert set(compose["services"]) == {
        "postgres",
        "backend",
        "frontend",
    }


def test_compose_preserves_secure_postgres_service() -> None:
    postgres = _compose()["services"]["postgres"]

    assert postgres["image"] == "postgres:18.6-bookworm"
    assert postgres["ports"] == ["127.0.0.1:${POSTGRES_PORT:-5432}:5432"]
    assert postgres["volumes"] == ["postgres_data:/var/lib/postgresql"]
    assert postgres["security_opt"] == ["no-new-privileges:true"]
    assert postgres["environment"]["POSTGRES_PASSWORD"].startswith("${")
    assert postgres["healthcheck"]["test"][0] == "CMD-SHELL"


def test_compose_backend_is_internal_non_privileged_and_health_gated() -> None:
    backend = _compose()["services"]["backend"]

    assert backend["build"]["dockerfile"] == "Dockerfile.backend"
    assert backend["env_file"] == [".env"]
    assert backend["ports"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert backend["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert backend["security_opt"] == ["no-new-privileges:true"]
    assert "${APP_DATABASE_PASSWORD:?required}" in (backend["environment"]["DATABASE_URL"])
    assert (
        "${ANALYTICS_DATABASE_PASSWORD:?required}"
        in (backend["environment"]["ANALYTICS_DATABASE_URL"])
    )
    assert backend["healthcheck"]["test"][0] == "CMD"


def test_compose_frontend_uses_internal_backend_and_health_dependency() -> None:
    frontend = _compose()["services"]["frontend"]

    assert frontend["build"]["dockerfile"] == "Dockerfile.frontend"
    assert frontend["env_file"] == [".env"]
    assert frontend["environment"]["API_BASE_URL"] == "http://backend:8000"
    assert frontend["ports"] == ["127.0.0.1:${FRONTEND_PORT:-8501}:8501"]
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"
    assert frontend["security_opt"] == ["no-new-privileges:true"]
    assert frontend["healthcheck"]["test"][0] == "CMD"


def test_compose_does_not_mount_database_or_docker_socket_into_app_services() -> None:
    services = _compose()["services"]

    for service_name in ("backend", "frontend"):
        volumes = services[service_name].get("volumes", [])
        serialized = "\n".join(str(volume) for volume in volumes)

        assert "/var/lib/postgresql" not in serialized
        assert "/var/run/docker.sock" not in serialized
