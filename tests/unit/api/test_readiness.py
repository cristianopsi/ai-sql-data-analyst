from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.health import router
from backend.app.core.config import Settings
from backend.app.db.pools import DatabasePools
from backend.app.main import create_app


class FakeConnection:
    def __init__(
        self,
        transaction_read_only: str,
    ) -> None:
        self.transaction_read_only = transaction_read_only

    def execute(
        self,
        statement: str,
    ) -> FakeConnection:
        assert "transaction_read_only" in statement

        return self

    def fetchone(self) -> tuple[str]:
        return (self.transaction_read_only,)


class FakePool:
    def __init__(
        self,
        transaction_read_only: str,
        *,
        failure_message: str | None = None,
    ) -> None:
        self.transaction_read_only = transaction_read_only
        self.failure_message = failure_message
        self.closed = True

    def open(
        self,
        *,
        wait: bool,
        timeout: float,
    ) -> None:
        assert wait is True
        assert timeout == 1
        self.closed = False

    def close(self) -> None:
        self.closed = True

    @contextmanager
    def connection(
        self,
    ) -> Iterator[FakeConnection]:
        if self.failure_message is not None:
            raise RuntimeError(self.failure_message)

        yield FakeConnection(self.transaction_read_only)


def create_readiness_application(
    *,
    application_read_only: str = "off",
    analytics_read_only: str = "on",
    analytics_failure: str | None = None,
) -> FastAPI:
    settings = Settings(
        _env_file=None,
        database_pool_timeout_seconds=1,
    )
    pools = DatabasePools(
        application=FakePool(application_read_only),
        analytics=FakePool(
            analytics_read_only,
            failure_message=analytics_failure,
        ),
        open_timeout_seconds=1,
    )

    return create_app(
        settings=settings,
        pool_factory=lambda configured_settings: pools,
    )


def test_readiness_returns_ready_when_both_pools_are_valid() -> None:
    application = create_readiness_application()

    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "application_database": {
            "status": "ok",
        },
        "analytics_database": {
            "status": "ok",
        },
    }


def test_readiness_sanitizes_database_failure() -> None:
    sensitive_failure = "connection failed with database-password-marker"
    application = create_readiness_application(analytics_failure=sensitive_failure)

    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "application_database": {
            "status": "ok",
        },
        "analytics_database": {
            "status": "unavailable",
        },
    }
    assert sensitive_failure not in response.text
    assert "database-password-marker" not in (response.text)


def test_readiness_rejects_wrong_analytics_transaction_mode() -> None:
    application = create_readiness_application(analytics_read_only="off")

    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == ("not_ready")
    assert response.json()["analytics_database"] == {
        "status": "unavailable",
    }


def test_readiness_is_unavailable_before_lifespan() -> None:
    application = FastAPI()
    application.state.database_ready = False
    application.include_router(router)

    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "application_database": {
            "status": "unavailable",
        },
        "analytics_database": {
            "status": "unavailable",
        },
    }
