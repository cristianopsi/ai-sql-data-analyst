from collections.abc import MutableSequence

import pytest

from backend.app.core.config import Settings
from backend.app.db.pools import (
    DatabasePoolConfigurationError,
    DatabasePools,
    create_database_pools,
    normalize_database_url,
)


class FakePool:
    def __init__(
        self,
        name: str,
        events: MutableSequence[str],
        *,
        fail_open: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fail_open = fail_open

    def open(
        self,
        *,
        wait: bool,
        timeout: float,
    ) -> None:
        self.events.append(f"{self.name}:open:wait={wait}:timeout={timeout}")

        if self.fail_open:
            raise RuntimeError(f"{self.name} unavailable")

    def close(self) -> None:
        self.events.append(f"{self.name}:close")


def test_sqlalchemy_postgresql_url_is_normalized() -> None:
    normalized = normalize_database_url(
        ("postgresql+psycopg://runtime_user:runtime-secret@127.0.0.1:5432/runtime_database"),
        "DATABASE_URL",
    )

    assert normalized == (
        "postgresql://runtime_user:runtime-secret@127.0.0.1:5432/runtime_database"
    )


def test_missing_database_url_is_rejected() -> None:
    with pytest.raises(
        DatabasePoolConfigurationError,
        match="DATABASE_URL must be configured",
    ):
        normalize_database_url(None, "DATABASE_URL")


def test_non_postgresql_database_url_is_rejected() -> None:
    with pytest.raises(
        DatabasePoolConfigurationError,
        match="ANALYTICS_DATABASE_URL must use PostgreSQL",
    ):
        normalize_database_url(
            "sqlite:///analytics.db",
            "ANALYTICS_DATABASE_URL",
        )


def test_database_pools_are_created_closed() -> None:
    application_password = "application-test-secret"
    analytics_password = "analytics-test-secret"

    settings = Settings(
        _env_file=None,
        database_url=(
            f"postgresql+psycopg://application:{application_password}@127.0.0.1:5432/database"
        ),
        analytics_database_url=(
            f"postgresql+psycopg://analytics:{analytics_password}@127.0.0.1:5432/database"
        ),
        database_pool_min_size=0,
        database_pool_max_size=2,
        database_pool_timeout_seconds=4,
    )

    pools = create_database_pools(settings)

    try:
        assert pools.application.name == "application-database"
        assert pools.analytics.name == "analytics-database"
        assert pools.application.min_size == 0
        assert pools.application.max_size == 2
        assert pools.analytics.min_size == 0
        assert pools.analytics.max_size == 2
        assert pools.application.closed is True
        assert pools.analytics.closed is True
        assert application_password not in repr(pools)
        assert analytics_password not in repr(pools)
    finally:
        pools.close()


def test_database_pools_open_and_close_in_order() -> None:
    events: list[str] = []
    application = FakePool("application", events)
    analytics = FakePool("analytics", events)

    pools = DatabasePools(
        application=application,
        analytics=analytics,
        open_timeout_seconds=5,
    )

    pools.open()
    pools.close()

    assert events == [
        "application:open:wait=True:timeout=5",
        "analytics:open:wait=True:timeout=5",
        "analytics:close",
        "application:close",
    ]


def test_database_pool_startup_failure_closes_both_pools() -> None:
    events: list[str] = []
    application = FakePool("application", events)
    analytics = FakePool(
        "analytics",
        events,
        fail_open=True,
    )

    pools = DatabasePools(
        application=application,
        analytics=analytics,
        open_timeout_seconds=5,
    )

    with pytest.raises(
        RuntimeError,
        match="analytics unavailable",
    ):
        pools.open()

    assert events == [
        "application:open:wait=True:timeout=5",
        "analytics:open:wait=True:timeout=5",
        "analytics:close",
        "application:close",
    ]
