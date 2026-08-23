from dataclasses import dataclass, field
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend.app.core.config import Settings

type RuntimeConnectionPool = ConnectionPool[Connection[Any]]


class DatabasePoolConfigurationError(ValueError):
    """Raised when a runtime database pool cannot be configured safely."""


@dataclass(slots=True)
class DatabasePools:
    application: RuntimeConnectionPool = field(repr=False)
    analytics: RuntimeConnectionPool = field(repr=False)
    open_timeout_seconds: float

    def open(self) -> None:
        """Open both pools and fail atomically if either pool is unavailable."""
        try:
            self.application.open(
                wait=True,
                timeout=self.open_timeout_seconds,
            )
            self.analytics.open(
                wait=True,
                timeout=self.open_timeout_seconds,
            )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Close both pools, even if closing the analytics pool fails."""
        try:
            self.analytics.close()
        finally:
            self.application.close()


def normalize_database_url(
    database_url: str | None,
    variable_name: str,
) -> str:
    """Convert a SQLAlchemy PostgreSQL URL into Psycopg connection info."""
    if database_url is None or not database_url.strip():
        raise DatabasePoolConfigurationError(f"{variable_name} must be configured")

    try:
        parsed_url = make_url(database_url)
    except ArgumentError as error:
        raise DatabasePoolConfigurationError(
            f"{variable_name} is not a valid database URL"
        ) from error

    if parsed_url.get_backend_name() != "postgresql":
        raise DatabasePoolConfigurationError(f"{variable_name} must use PostgreSQL")

    return parsed_url.set(drivername="postgresql").render_as_string(hide_password=False)


def _create_pool(
    database_url: str | None,
    variable_name: str,
    pool_name: str,
    settings: Settings,
) -> RuntimeConnectionPool:
    connection_info = normalize_database_url(
        database_url,
        variable_name,
    )

    return ConnectionPool(
        conninfo=connection_info,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        timeout=settings.database_pool_timeout_seconds,
        kwargs={"connect_timeout": (settings.database_connect_timeout_seconds)},
        check=ConnectionPool.check_connection,
        name=pool_name,
        open=False,
    )


def create_database_pools(
    settings: Settings,
) -> DatabasePools:
    """Build unopened application and analytics connection pools."""
    application_pool = _create_pool(
        settings.database_url,
        "DATABASE_URL",
        "application-database",
        settings,
    )
    analytics_pool = _create_pool(
        settings.analytics_database_url,
        "ANALYTICS_DATABASE_URL",
        "analytics-database",
        settings,
    )

    return DatabasePools(
        application=application_pool,
        analytics=analytics_pool,
        open_timeout_seconds=(settings.database_pool_timeout_seconds),
    )
