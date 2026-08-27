import re
from pathlib import Path
from typing import Any, cast

import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


def _as_mapping(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _as_list(value: object) -> list[Any]:
    assert isinstance(value, list)
    return value


def _workflow_source() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    loaded: object = yaml.load(
        _workflow_source(),
        Loader=yaml.BaseLoader,
    )
    return _as_mapping(loaded)


def _jobs() -> dict[str, Any]:
    return _as_mapping(_workflow()["jobs"])


def _steps(job_name: str) -> list[dict[str, Any]]:
    job = _as_mapping(_jobs()[job_name])
    return [_as_mapping(step) for step in _as_list(job["steps"])]


def _run_source(job_name: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job_name))


def test_ci_workflow_exists_and_parses_safely() -> None:
    assert _WORKFLOW_PATH.is_file()
    assert _workflow()["name"] == "ci"


def test_ci_workflow_triggers_are_exact() -> None:
    triggers = _as_mapping(_workflow()["on"])

    assert set(triggers) == {
        "push",
        "pull_request",
        "workflow_dispatch",
    }
    assert _as_mapping(triggers["push"])["branches"] == ["main"]


def test_ci_workflow_permissions_are_read_only() -> None:
    permissions = _as_mapping(_workflow()["permissions"])

    assert permissions == {"contents": "read"}


def test_ci_workflow_cancels_stale_runs() -> None:
    concurrency = _as_mapping(_workflow()["concurrency"])

    assert concurrency["group"] == ("ci-${{ github.workflow }}-${{ github.ref }}")
    assert concurrency["cancel-in-progress"] == "true"


def test_ci_workflow_jobs_are_exact() -> None:
    assert set(_jobs()) == {"quality", "container"}


def test_ci_quality_job_uses_supported_python_matrix() -> None:
    quality = _as_mapping(_jobs()["quality"])
    strategy = _as_mapping(quality["strategy"])
    matrix = _as_mapping(strategy["matrix"])

    assert strategy["fail-fast"] == "false"
    assert matrix["python-version"] == ["3.12", "3.13"]


def test_ci_quality_commands_are_exact() -> None:
    source = _run_source("quality")

    assert 'python -m pip install -e ".[dev]"' in source
    assert "python -m ruff check ." in source
    assert "python -m ruff format --check ." in source
    assert (
        "python -m mypy backend frontend "
        "tests/unit/test_containerization.py "
        "tests/unit/test_ci_configuration.py"
    ) in " ".join(source.split())
    assert "python -m pytest -q" in source


def test_ci_job_timeouts_are_bounded() -> None:
    jobs = _jobs()

    assert int(_as_mapping(jobs["quality"])["timeout-minutes"]) == 20
    assert int(_as_mapping(jobs["container"])["timeout-minutes"]) == 30


def test_ci_uses_only_expected_official_actions() -> None:
    action_references = {
        str(step["uses"])
        for job_name in ("quality", "container")
        for step in _steps(job_name)
        if "uses" in step
    }

    assert action_references == {
        "actions/checkout@v4",
        "actions/setup-python@v5",
    }


def test_ci_workflow_contains_no_secret_references() -> None:
    source = _workflow_source()

    assert re.search(r"\$\{\{\s*secrets\.", source) is None
    assert "pull_request_target" not in source


def test_ci_builds_both_application_images() -> None:
    source = " ".join(_run_source("container").split())

    assert (
        "docker build --file Dockerfile.backend --tag ai-sql-data-analyst-backend:ci ."
    ) in source
    assert (
        "docker build --file Dockerfile.frontend --tag ai-sql-data-analyst-frontend:ci ."
    ) in source


def test_ci_compose_validation_uses_ephemeral_ignored_env() -> None:
    source = _run_source("container")

    assert ": > .env" in source
    assert "trap cleanup_ci_environment EXIT" in source
    assert "docker compose config --quiet" in source
    assert "secrets.token_urlsafe(32)" in source


def test_ci_container_smoke_has_no_network() -> None:
    source = " ".join(_run_source("container").split())

    assert source.count("docker run --rm --network none") == 2


def test_ci_does_not_push_container_images() -> None:
    source = _workflow_source()

    assert re.search(r"\bdocker\s+(?:image\s+)?push\b", source) is None
    assert "docker/build-push-action" not in source


def test_ci_does_not_call_external_llm_services() -> None:
    source = _workflow_source().lower()

    forbidden_values = (
        "openai_api_key",
        "gemini_api_key",
        "groq_api_key",
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "api.groq.com",
        "openrouter.ai",
    )

    assert all(value not in source for value in forbidden_values)


def test_compose_validation_provisions_exact_required_environment_variables() -> None:
    workflow_source = (_REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    compose_source = (_REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")

    expected_required_variables = {
        "ANALYTICS_DATABASE_PASSWORD",
        "ANALYTICS_DATABASE_USER",
        "APP_DATABASE_PASSWORD",
        "APP_DATABASE_USER",
        "POSTGRES_PASSWORD",
    }
    compose_required_variables = set(
        re.findall(
            r"\$\{([A-Z][A-Z0-9_]*):\?[^}]+\}",
            compose_source,
        )
    )
    workflow_exported_variables = set(
        re.findall(
            r"^\s*export\s+([A-Z][A-Z0-9_]*)=",
            workflow_source,
            flags=re.MULTILINE,
        )
    )

    assert compose_required_variables == expected_required_variables
    assert expected_required_variables <= workflow_exported_variables
    assert 'export APP_DATABASE_USER="ci_application"' in workflow_source
    assert 'export ANALYTICS_DATABASE_USER="ci_analytics"' in workflow_source
    assert 'export POSTGRES_PASSWORD="$(' in workflow_source
    assert 'export APP_DATABASE_PASSWORD="$(' in workflow_source
    assert 'export ANALYTICS_DATABASE_PASSWORD="$(' in workflow_source
