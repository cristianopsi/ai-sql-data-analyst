import pytest

from backend.app.db.role_provisioning import (
    DatabaseRoleSpec,
    RoleConfigurationError,
    build_role_specs,
    require_value,
    sqlalchemy_to_psycopg_url,
)

VALID_VALUES = {
    "APP_DATABASE_USER": "ai_sql_app",
    "APP_DATABASE_PASSWORD": "a" * 64,
    "ANALYTICS_DATABASE_USER": "ai_analyst_reader",
    "ANALYTICS_DATABASE_PASSWORD": "b" * 64,
}


def test_build_role_specs_defines_least_privilege_defaults() -> None:
    app_role, analytics_role = build_role_specs(VALID_VALUES)

    assert app_role.name == "ai_sql_app"
    assert app_role.connection_limit == 10
    assert dict(app_role.settings)["statement_timeout"] == "15000ms"

    assert analytics_role.name == "ai_analyst_reader"
    assert analytics_role.connection_limit == 10
    assert dict(analytics_role.settings) == {
        "default_transaction_read_only": "on",
        "statement_timeout": "8000ms",
        "lock_timeout": "2000ms",
        "idle_in_transaction_session_timeout": "30000ms",
    }


def test_role_repr_does_not_expose_password() -> None:
    password = "not-a-real-secret-" + ("x" * 32)
    role = DatabaseRoleSpec(
        name="safe_reader",
        password=password,
    )

    assert password not in repr(role)
    assert "password=" not in repr(role)


@pytest.mark.parametrize(
    "missing_key",
    [
        "APP_DATABASE_USER",
        "APP_DATABASE_PASSWORD",
        "ANALYTICS_DATABASE_USER",
        "ANALYTICS_DATABASE_PASSWORD",
    ],
)
def test_missing_role_configuration_is_rejected(
    missing_key: str,
) -> None:
    values = dict(VALID_VALUES)
    values[missing_key] = ""

    with pytest.raises(
        RoleConfigurationError,
        match=f"{missing_key} must be configured",
    ):
        build_role_specs(values)


def test_roles_must_use_different_names() -> None:
    values = dict(VALID_VALUES)
    values["ANALYTICS_DATABASE_USER"] = "ai_sql_app"

    with pytest.raises(
        RoleConfigurationError,
        match="must use different names",
    ):
        build_role_specs(values)


def test_roles_must_use_different_passwords() -> None:
    values = dict(VALID_VALUES)
    values["ANALYTICS_DATABASE_PASSWORD"] = "a" * 64

    with pytest.raises(
        RoleConfigurationError,
        match="must use different passwords",
    ):
        build_role_specs(values)


@pytest.mark.parametrize(
    "role_name",
    [
        "UPPERCASE",
        "contains-hyphen",
        "2_starts_with_number",
        "ab",
    ],
)
def test_invalid_role_names_are_rejected(role_name: str) -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="Invalid PostgreSQL role name",
    ):
        DatabaseRoleSpec(
            name=role_name,
            password="x" * 64,
        )


def test_short_password_is_rejected() -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="must have at least 32 characters",
    ):
        DatabaseRoleSpec(
            name="safe_reader",
            password="short",
        )


def test_sqlalchemy_url_is_converted_for_psycopg() -> None:
    converted = sqlalchemy_to_psycopg_url(
        "postgresql+psycopg://user:secret@127.0.0.1:5432/database"
    )

    assert converted == ("postgresql://user:secret@127.0.0.1:5432/database")


def test_non_postgresql_url_is_rejected() -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="must use PostgreSQL",
    ):
        sqlalchemy_to_psycopg_url("sqlite:///database.db")


def test_require_value_rejects_empty_value() -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="REQUIRED_VALUE must be configured",
    ):
        require_value({"REQUIRED_VALUE": None}, "REQUIRED_VALUE")
