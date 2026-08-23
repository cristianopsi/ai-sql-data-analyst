from collections.abc import MutableSequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.lifecycle import (
    create_database_lifespan,
)


class FakeDatabasePools:
    def __init__(
        self,
        events: MutableSequence[str],
        *,
        fail_open: bool = False,
    ) -> None:
        self.events = events
        self.fail_open = fail_open

    def open(self) -> None:
        self.events.append("pools:open")

        if self.fail_open:
            self.events.append("pools:startup-rollback")
            raise RuntimeError("database startup failed")

    def close(self) -> None:
        self.events.append("pools:close")


def test_lifespan_opens_and_closes_database_pools() -> None:
    events: list[str] = []
    settings = Settings(_env_file=None)
    pools = FakeDatabasePools(events)
    received_settings: list[Settings] = []

    def pool_factory(
        configured_settings: Settings,
    ) -> FakeDatabasePools:
        received_settings.append(configured_settings)

        return pools

    application = FastAPI(
        lifespan=create_database_lifespan(
            settings,
            pool_factory,
        )
    )

    assert not hasattr(
        application.state,
        "database_ready",
    )

    with TestClient(application):
        assert application.state.database_ready is True
        assert application.state.database_pools is pools
        assert events == ["pools:open"]

    assert application.state.database_ready is False
    assert events == [
        "pools:open",
        "pools:close",
    ]
    assert received_settings == [settings]


def test_lifespan_preserves_failed_startup_state() -> None:
    events: list[str] = []
    settings = Settings(_env_file=None)
    pools = FakeDatabasePools(
        events,
        fail_open=True,
    )

    application = FastAPI(
        lifespan=create_database_lifespan(
            settings,
            lambda configured_settings: pools,
        )
    )

    with (
        pytest.raises(
            RuntimeError,
            match="database startup failed",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert application.state.database_ready is False
    assert application.state.database_pools is pools
    assert events == [
        "pools:open",
        "pools:startup-rollback",
    ]
