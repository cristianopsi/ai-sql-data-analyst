from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.grounding import (
    router,
)
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)


class StubDatabasePools:
    def open(self) -> None:
        return None

    def close(self) -> None:
        return None


def create_stub_pools(
    settings: Settings,
) -> StubDatabasePools:
    del settings
    return StubDatabasePools()


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        max_question_length=100,
    )
    application = create_app(
        settings=settings,
        pool_factory=create_stub_pools,
    )

    with TestClient(application) as test_client:
        yield test_client


def test_grounded_question_returns_safe_context(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/grounding/context",
        json={"question": ("Qual foi o faturamento por região em 2025?")},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-grounding-status"] == "grounded"
    assert response.headers["x-semantic-version"] == "1"
    assert response.headers["x-catalog-version"] == "1"

    body = response.json()

    assert body["grounding_status"] == ("grounded")
    assert [metric["name"] for metric in body["metrics"]] == ["approved_revenue"]
    assert [table["name"] for table in body["tables"]] == [
        "orders",
        "payments",
        "regions",
    ]

    serialized = response.text

    assert "email" not in serialized
    assert "document_number" not in serialized
    assert "sql" not in body


def test_restricted_question_returns_sanitized_403(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/grounding/context",
        json={"question": ("Liste os emails dos clientes")},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": ("Question cannot be grounded safely")}
    assert "email" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_unsupported_question_returns_422(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/grounding/context",
        json={"question": ("Qual é a temperatura hoje?")},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": ("Question is outside the supported analytics domain")}


@pytest.mark.parametrize(
    "question",
    [
        "   ",
        "!!!",
        "x" * 101,
    ],
)
def test_invalid_question_is_sanitized(
    client: TestClient,
    question: str,
) -> None:
    response = client.post(
        "/api/v1/grounding/context",
        json={"question": question},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Question is invalid"}
    stripped_question = question.strip()

    if stripped_question:
        assert stripped_question not in (response.text)


def test_missing_service_returns_503() -> None:
    application = FastAPI()
    application.include_router(router)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/grounding/context",
            json={"question": "Pedidos por região"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": ("Grounding context is unavailable")}


def test_service_failure_is_sanitized() -> None:
    class FailingService:
        def build(
            self,
            question: str,
        ) -> CompactGroundingContext:
            del question
            raise RuntimeError("internal sensitive failure")

    application = FastAPI()
    application.include_router(router)
    application.state.grounding_context_service = FailingService()

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/grounding/context",
            json={"question": "Pedidos por região"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": ("Grounding context is unavailable")}
    assert "internal sensitive failure" not in response.text


def test_openapi_documents_grounding_contract(
    client: TestClient,
) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200

    operation = response.json()["paths"]["/api/v1/grounding/context"]["post"]

    assert {
        "200",
        "403",
        "422",
        "503",
    } <= set(operation["responses"])
