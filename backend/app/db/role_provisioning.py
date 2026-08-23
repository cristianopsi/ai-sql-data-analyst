from collections.abc import Mapping
from dataclasses import dataclass, field
from re import fullmatch
from typing import Any

from psycopg import Connection, sql
from sqlalchemy.engine import make_url

ROLE_NAME_PATTERN = r"[a-z][a-z0-9_]{2,62}"

ALLOWED_ROLE_SETTINGS = frozenset(
    {
        "default_transaction_read_only",
        "idle_in_transaction_session_timeout",
        "lock_timeout",
        "statement_timeout",
    }
)


class RoleConfigurationError(ValueError):
    """Raised when database role configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class DatabaseRoleSpec:
    name: str
    password: str = field(repr=False)
    connection_limit: int = 10
    settings: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if fullmatch(ROLE_NAME_PATTERN, self.name) is None:
            raise RoleConfigurationError(f"Invalid PostgreSQL role name: {self.name!r}")

        if len(self.password) < 32:
            raise RoleConfigurationError(
                f"Password for role {self.name!r} must have at least 32 characters"
            )

        if not 1 <= self.connection_limit <= 50:
            raise RoleConfigurationError(
                f"Connection limit for role {self.name!r} must be between 1 and 50"
            )

        unsupported_settings = {
            setting for setting, _ in self.settings if setting not in ALLOWED_ROLE_SETTINGS
        }

        if unsupported_settings:
            raise RoleConfigurationError(
                "Unsupported role settings: " + ", ".join(sorted(unsupported_settings))
            )


def require_value(
    values: Mapping[str, str | None],
    key: str,
) -> str:
    value = values.get(key)

    if not value:
        raise RoleConfigurationError(f"{key} must be configured")

    return value


def build_role_specs(
    values: Mapping[str, str | None],
) -> tuple[DatabaseRoleSpec, DatabaseRoleSpec]:
    app_role = DatabaseRoleSpec(
        name=require_value(values, "APP_DATABASE_USER"),
        password=require_value(values, "APP_DATABASE_PASSWORD"),
        connection_limit=10,
        settings=(
            ("statement_timeout", "15000ms"),
            ("lock_timeout", "3000ms"),
            ("idle_in_transaction_session_timeout", "60000ms"),
        ),
    )

    analytics_role = DatabaseRoleSpec(
        name=require_value(values, "ANALYTICS_DATABASE_USER"),
        password=require_value(values, "ANALYTICS_DATABASE_PASSWORD"),
        connection_limit=10,
        settings=(
            ("default_transaction_read_only", "on"),
            ("statement_timeout", "8000ms"),
            ("lock_timeout", "2000ms"),
            ("idle_in_transaction_session_timeout", "30000ms"),
        ),
    )

    if app_role.name == analytics_role.name:
        raise RoleConfigurationError("Application and analytics roles must use different names")

    if app_role.password == analytics_role.password:
        raise RoleConfigurationError("Application and analytics roles must use different passwords")

    return app_role, analytics_role


def sqlalchemy_to_psycopg_url(database_url: str) -> str:
    url = make_url(database_url)

    if url.get_backend_name() != "postgresql":
        raise RoleConfigurationError("MIGRATION_DATABASE_URL must use PostgreSQL")

    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _role_exists(
    connection: Connection[Any],
    role_name: str,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
            (role_name,),
        )
        row = cursor.fetchone()

    if row is None:
        raise RuntimeError("PostgreSQL did not return the role existence result")

    return bool(row[0])


def _apply_role_attributes(
    connection: Connection[Any],
    role: DatabaseRoleSpec,
    *,
    create: bool,
) -> None:
    operation = "CREATE ROLE" if create else "ALTER ROLE"

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                f"""
                {operation} {{}}
                WITH
                    LOGIN
                    NOSUPERUSER
                    NOCREATEDB
                    NOCREATEROLE
                    NOINHERIT
                    NOREPLICATION
                    NOBYPASSRLS
                    CONNECTION LIMIT {{}}
                """
            ).format(
                sql.Identifier(role.name),
                sql.Literal(role.connection_limit),
            )
        )

        cursor.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(role.name),
                sql.Literal(role.password),
            )
        )


def _apply_role_settings(
    connection: Connection[Any],
    database_name: str,
    role: DatabaseRoleSpec,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ALL").format(
                sql.Identifier(role.name),
                sql.Identifier(database_name),
            )
        )

        for setting_name, setting_value in role.settings:
            cursor.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET {} TO {}").format(
                    sql.Identifier(role.name),
                    sql.Identifier(database_name),
                    sql.SQL(setting_name),
                    sql.Literal(setting_value),
                )
            )


def _apply_database_privileges(
    connection: Connection[Any],
    database_name: str,
    roles: tuple[DatabaseRoleSpec, ...],
) -> None:
    role_identifiers = sql.SQL(", ").join(sql.Identifier(role.name) for role in roles)

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database_name)
            )
        )
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name),
                role_identifiers,
            )
        )
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def provision_database_roles(
    connection: Connection[Any],
    database_name: str,
    roles: tuple[DatabaseRoleSpec, ...],
) -> tuple[str, ...]:
    if not roles:
        raise RoleConfigurationError("At least one database role is required")

    actions: list[str] = []

    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL log_min_duration_statement = -1")

        for role in roles:
            exists = _role_exists(connection, role.name)
            _apply_role_attributes(
                connection,
                role,
                create=not exists,
            )
            _apply_role_settings(connection, database_name, role)
            actions.append(f"{role.name}:{'updated' if exists else 'created'}")

        _apply_database_privileges(connection, database_name, roles)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return tuple(actions)
