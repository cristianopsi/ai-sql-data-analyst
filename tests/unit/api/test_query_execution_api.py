from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.query_execution import (
    router,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)
from backend.app.services.grounding_context import (
    GroundingContextError,
)
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
)
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
)
from backend.app.services.text_to_sql import (
    TextToSQLUnavailableError,
)


def build_generation_result() -> SQLGenerationResult:
    validated = ValidatedSQL.model_construct(
        validation_version="1",
        validation_status="validated",
        context_version="1",
        semantic_version="1",
        catalog_version="1",
        sql=("SELECT o.id FROM retail.orders AS o LIMIT 1"),
        row_limit=1,
        referenced_tables=("orders",),
        referenced_columns=("orders.id",),
    )

    return SQLGenerationResult.model_construct(
        generation_version="1",
        validated_sql=validated,
        generation_attempts=1,
        repair_attempts=0,
    )


def build_execution_result(
    generation: SQLGenerationResult,
) -> QueryExecutionResult:
    return QueryExecutionResult(
        generation=generation,
        columns=("id",),
        rows=((1,),),
        row_count=1,
        statement_timeout_ms=8000,
        query_timeout_seconds=10.0,
        execution_time_ms=1.5,
    )


class StubGenerationPipeline:
    def __init__(
        self,
        generation: SQLGenerationResult,
        *,
        error: Exception | None = None,
    ) -> None:
        self.generation = generation
        self.error = error
        self.questions: list[str] = []

    def generate(
        self,
        question: str,
    ) -> SQLGenerationResult:
        self.questions.append(question)

        if self.error is not None:
            raise self.error

        return self.generation


class StubQueryExecutor:
    def __init__(
        self,
        result: QueryExecutionResult,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.generations: list[SQLGenerationResult] = []

    def execute(
        self,
        generation: SQLGenerationResult,
    ) -> QueryExecutionResult:
        self.generations.append(generation)

        if self.error is not None:
            raise self.error

        return self.result


@contextmanager
def controlled_client(
    pipeline: StubGenerationPipeline | None,
    executor: StubQueryExecutor | None,
    *,
    database_ready: bool = True,
) -> Iterator[TestClient]:
    application = FastAPI()
    application.include_router(router)
    application.state.database_ready = database_ready

    if pipeline is not None:
        application.state.sql_generation_pipeline = pipeline

    if executor is not None:
        application.state.query_executor = executor

    with TestClient(application) as client:
        yield client


def test_execute_endpoint_generates_then_executes() -> None:
    generation = build_generation_result()
    result = build_execution_result(generation)
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(result)

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={
                "question": ("Mostre um pedido"),
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-query-execution-version"] == "1"
    assert response.headers["x-sql-generation-version"] == "1"
    assert response.headers["x-sql-validation-version"] == "1"
    assert response.headers["x-catalog-version"] == "1"
    assert response.headers["x-semantic-version"] == "1"

    body = response.json()

    assert body["execution_status"] == "executed"
    assert body["transaction_read_only"] is True
    assert body["columns"] == ["id"]
    assert body["rows"] == [[1]]
    assert body["row_count"] == 1
    assert pipeline.questions == ["Mostre um pedido"]
    assert executor.generations == [generation]


def test_invalid_request_is_sanitized() -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(build_execution_result(generation))

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={
                "question": "   ",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert response.headers["cache-control"] == "no-store"
    assert pipeline.questions == []
    assert executor.generations == []


def test_question_failure_returns_sanitized_422() -> None:
    sensitive = "invalid-question-sensitive"
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(
        generation,
        error=GroundingContextError(sensitive),
    )
    executor = StubQueryExecutor(build_execution_result(generation))

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={"question": "Pergunta inválida"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert sensitive not in response.text
    assert executor.generations == []


def test_unsafe_generation_returns_sanitized_422() -> None:
    sensitive = "rejected-sql-sensitive"
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(
        generation,
        error=SQLGenerationExhaustedError(sensitive),
    )
    executor = StubQueryExecutor(build_execution_result(generation))

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={"question": "Pedidos"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": ("Query could not be generated safely"),
    }
    assert sensitive not in response.text
    assert executor.generations == []


def test_generation_unavailable_returns_503() -> None:
    sensitive = "provider-sensitive"
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(
        generation,
        error=TextToSQLUnavailableError(sensitive),
    )
    executor = StubQueryExecutor(build_execution_result(generation))

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={"question": "Pedidos"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Query execution is unavailable",
    }
    assert sensitive not in response.text
    assert executor.generations == []


@pytest.mark.parametrize(
    "execution_error",
    (
        QueryExecutionSecurityError("read-only-sensitive"),
        QueryExecutionResultError("result-sensitive"),
    ),
)
def test_unsafe_execution_returns_sanitized_422(
    execution_error: Exception,
) -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(
        build_execution_result(generation),
        error=execution_error,
    )

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={"question": "Pedidos"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": ("Query could not be executed safely"),
    }
    assert str(execution_error) not in response.text
    assert pipeline.questions == ["Pedidos"]
    assert executor.generations == [generation]


def test_execution_unavailable_returns_503() -> None:
    sensitive = "database-sensitive"
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(
        build_execution_result(generation),
        error=QueryExecutionUnavailableError(sensitive),
    )

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={"question": "Pedidos"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Query execution is unavailable",
    }
    assert sensitive not in response.text


@pytest.mark.parametrize(
    (
        "pipeline_present",
        "executor_present",
        "database_ready",
    ),
    (
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ),
)
def test_missing_runtime_dependency_returns_503(
    pipeline_present: bool,
    executor_present: bool,
    database_ready: bool,
) -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation) if pipeline_present else None
    executor = StubQueryExecutor(build_execution_result(generation)) if executor_present else None

    with controlled_client(
        pipeline,
        executor,
        database_ready=database_ready,
    ) as client:
        response = client.post(
            "/api/v1/query/execute",
            json={"question": "Pedidos"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Query execution is unavailable",
    }


def test_openapi_documents_query_execution() -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(build_execution_result(generation))

    with controlled_client(
        pipeline,
        executor,
    ) as client:
        document = client.get("/openapi.json").json()

    operation = document["paths"]["/api/v1/query/execute"]["post"]

    assert operation["summary"] == ("Generate and execute validated read-only SQL")
    assert set(operation["responses"]) >= {
        "200",
        "422",
        "503",
    }

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert request_schema["$ref"].endswith("/QueryExecutionRequest")
