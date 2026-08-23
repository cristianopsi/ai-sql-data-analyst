import json
from collections.abc import (
    Callable,
    Iterator,
)
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.sql_generation import (
    router,
)
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas.llm import (
    LLMGenerationRequest,
)
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)

SAFE_SQL = (
    "SELECT "
    "r.name AS region, "
    "SUM(p.amount) AS approved_revenue "
    "FROM retail.payments AS p "
    "JOIN retail.orders AS o "
    "ON o.id = p.order_id "
    "JOIN retail.regions AS r "
    "ON r.id = o.region_id "
    "WHERE p.status = 'approved' "
    "GROUP BY r.name"
)


class StubDatabasePools:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def payload(
    sql: str,
) -> str:
    return json.dumps(
        {
            "sql": sql,
            "explanation": ("Controlled API SQL generation."),
        }
    )


@contextmanager
def controlled_client(
    responder: Callable[
        [LLMGenerationRequest],
        str,
    ],
    *,
    max_repair_attempts: int = 2,
) -> Iterator[
    tuple[
        TestClient,
        DeterministicMockLLMProvider,
        StubDatabasePools,
    ]
]:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="api-model",
        responder=responder,
    )

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings

        return pools

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        return provider

    application = create_app(
        settings=Settings(
            max_result_rows=200,
            max_sql_repair_attempts=(max_repair_attempts),
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
    )

    with TestClient(application) as client:
        yield (
            client,
            provider,
            pools,
        )

    assert provider.is_closed is True
    assert pools.closed is True


def test_generate_endpoint_returns_validated_sql() -> None:
    with controlled_client(lambda request: payload(SAFE_SQL)) as (
        client,
        provider,
        _,
    ):
        response = client.post(
            "/api/v1/sql/generate",
            json={
                "question": ("Faturamento por região em 2025"),
            },
        )

        assert response.status_code == 200
        body = response.json()

        assert body["generation_version"] == "1"
        assert body["generation_attempts"] == 1
        assert body["repair_attempts"] == 0
        assert body["validated_sql"]["validation_status"] == "validated"
        assert body["validated_sql"]["row_limit"] == 200
        assert body["validated_sql"]["sql"].endswith("LIMIT 200")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-sql-generation-version"] == "1"
        assert response.headers["x-sql-validation-version"] == "1"
        assert provider.generation_count == 1


def test_generate_endpoint_repairs_invalid_sql() -> None:
    requests: list[LLMGenerationRequest] = []

    def responder(
        request: LLMGenerationRequest,
    ) -> str:
        requests.append(request)

        if len(requests) == 1:
            return payload("SELECT * FROM retail.orders")

        return payload(SAFE_SQL)

    with controlled_client(responder) as (
        client,
        provider,
        _,
    ):
        response = client.post(
            "/api/v1/sql/generate",
            json={
                "question": ("Faturamento por região em 2025"),
            },
        )

        assert response.status_code == 200
        body = response.json()

        assert body["generation_attempts"] == 2
        assert body["repair_attempts"] == 1
        assert provider.generation_count == 2


def test_restricted_question_returns_sanitized_422() -> None:
    with controlled_client(lambda request: payload(SAFE_SQL)) as (
        client,
        provider,
        _,
    ):
        question = "Liste os emails dos clientes"
        response = client.post(
            "/api/v1/sql/generate",
            json={"question": question},
        )

        assert response.status_code == 422
        assert response.json() == {"detail": ("SQL could not be generated safely")}
        assert question not in response.text
        assert provider.generation_count == 0


def test_invalid_question_returns_sanitized_422() -> None:
    with controlled_client(lambda request: payload(SAFE_SQL)) as (
        client,
        provider,
        _,
    ):
        response = client.post(
            "/api/v1/sql/generate",
            json={"question": "   "},
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "Question is invalid"}
        assert provider.generation_count == 0


def test_exhausted_repair_returns_sanitized_422() -> None:
    rejected_sql = "SELECT * FROM retail.orders"

    with controlled_client(
        lambda request: payload(rejected_sql),
        max_repair_attempts=1,
    ) as (
        client,
        provider,
        _,
    ):
        response = client.post(
            "/api/v1/sql/generate",
            json={
                "question": ("Faturamento por região em 2025"),
            },
        )

        assert response.status_code == 422
        assert response.json() == {"detail": ("SQL could not be generated safely")}
        assert rejected_sql not in response.text
        assert provider.generation_count == 2


def test_provider_failure_returns_sanitized_503() -> None:
    sensitive_detail = "provider-secret-must-not-leak"

    def failing_responder(
        request: LLMGenerationRequest,
    ) -> str:
        del request

        raise RuntimeError(sensitive_detail)

    with controlled_client(failing_responder) as (
        client,
        provider,
        _,
    ):
        response = client.post(
            "/api/v1/sql/generate",
            json={
                "question": ("Faturamento por região em 2025"),
            },
        )

        assert response.status_code == 503
        assert response.json() == {"detail": ("SQL generation is unavailable")}
        assert sensitive_detail not in response.text
        assert provider.generation_count == 0


def test_missing_pipeline_returns_503() -> None:
    application = FastAPI()
    application.include_router(router)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/sql/generate",
            json={
                "question": ("Faturamento por região"),
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "SQL generation is unavailable"}


def test_openapi_documents_sql_generation() -> None:
    with controlled_client(lambda request: payload(SAFE_SQL)) as (
        client,
        _,
        _,
    ):
        document = client.get("/openapi.json").json()

    operation = document["paths"]["/api/v1/sql/generate"]["post"]

    assert "200" in operation["responses"]
    assert "422" in operation["responses"]
    assert "503" in operation["responses"]
