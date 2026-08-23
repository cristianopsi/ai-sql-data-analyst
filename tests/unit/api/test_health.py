from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


class StubDatabasePools:
    def open(self) -> None:
        return None

    def close(self) -> None:
        return None


def create_stub_database_pools(
    settings: Settings,
) -> StubDatabasePools:
    del settings

    return StubDatabasePools()


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = create_app(pool_factory=create_stub_database_pools)

    with TestClient(application) as test_client:
        yield test_client


def test_health_returns_service_metadata(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AI SQL Data Analyst",
        "version": "0.1.0",
        "environment": "development",
    }


def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:8501"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ("http://localhost:8501")


def test_cors_does_not_allow_unconfigured_origin(client: TestClient) -> None:
    response = client.get(
        "/health",
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_openapi_documents_health_endpoint(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200

    document = response.json()

    assert document["info"]["title"] == "AI SQL Data Analyst"
    assert document["info"]["version"] == "0.1.0"
    assert "/health" in document["paths"]
    assert "get" in document["paths"]["/health"]
    assert "/ready" in document["paths"]
    assert "get" in document["paths"]["/ready"]


def test_interactive_documentation_is_available(client: TestClient) -> None:
    swagger = client.get("/docs")
    redoc = client.get("/redoc")

    assert swagger.status_code == 200
    assert redoc.status_code == 200
    assert swagger.headers["content-type"].startswith("text/html")
    assert redoc.headers["content-type"].startswith("text/html")
