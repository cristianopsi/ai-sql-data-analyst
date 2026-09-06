from collections.abc import (
    Callable,
    Coroutine,
)
from typing import Any, cast

from fastapi import (
    APIRouter,
    Request,
    Response,
    status,
)
from fastapi.exceptions import (
    RequestValidationError,
)
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.query_execution import (
    QueryExecutionApiErrorDetail,
    QueryExecutionApiErrorResponse,
    QueryExecutionRequest,
    QueryExecutionResult,
)
from backend.app.services.grounding_context import (
    GroundingContextError,
)
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
    QueryExecutor,
)
from backend.app.services.question_grounding import (
    QuestionGroundingError,
)
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
    SQLGenerationPipeline,
)
from backend.app.services.text_to_sql import (
    TextToSQLGroundingError,
    TextToSQLResponseError,
    TextToSQLUnavailableError,
)

type RouteHandler = Callable[
    [Request],
    Coroutine[Any, Any, Response],
]

NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
}


def _error_response(
    detail: QueryExecutionApiErrorDetail,
    status_code: int,
) -> JSONResponse:
    response = QueryExecutionApiErrorResponse(
        detail=detail,
    )

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers=NO_STORE_HEADERS,
    )


class SanitizedQueryExecutionRoute(APIRoute):
    """Sanitize request-contract validation failures."""

    def get_route_handler(self) -> RouteHandler:
        original_handler = super().get_route_handler()

        async def sanitized_handler(
            request: Request,
        ) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError:
                return _error_response(
                    "Question is invalid",
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

        return sanitized_handler


router = APIRouter(
    prefix="/api/v1/query",
    tags=["query-execution"],
    route_class=SanitizedQueryExecutionRoute,
)


def _generate_and_execute(
    pipeline: SQLGenerationPipeline,
    executor: QueryExecutor,
    question: str,
) -> QueryExecutionResult:
    generation = pipeline.generate(question)

    return executor.execute(generation)


@router.post(
    "/execute",
    response_model=QueryExecutionResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": QueryExecutionApiErrorResponse,
            "description": (
                "The question, generated SQL, or query result could not pass controlled validation."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": QueryExecutionApiErrorResponse,
            "description": ("The managed generation or query-execution service is unavailable."),
        },
    },
    summary="Generate and execute validated read-only SQL",
)
async def execute_query(
    payload: QueryExecutionRequest,
    request: Request,
) -> Response:
    pipeline_value = getattr(
        request.app.state,
        "sql_generation_pipeline",
        None,
    )
    executor_value = getattr(
        request.app.state,
        "query_executor",
        None,
    )
    database_ready = getattr(
        request.app.state,
        "database_ready",
        False,
    )

    if pipeline_value is None or executor_value is None or database_ready is not True:
        return _error_response(
            "Query execution is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    pipeline = cast(
        SQLGenerationPipeline,
        pipeline_value,
    )
    executor = cast(
        QueryExecutor,
        executor_value,
    )

    try:
        result = await run_in_threadpool(
            _generate_and_execute,
            pipeline,
            executor,
            payload.question,
        )
    except (
        GroundingContextError,
        QuestionGroundingError,
    ):
        return _error_response(
            "Question is invalid",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        SQLGenerationExhaustedError,
        TextToSQLGroundingError,
        TextToSQLResponseError,
    ):
        return _error_response(
            "Query could not be generated safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        QueryExecutionResultError,
        QueryExecutionSecurityError,
    ):
        return _error_response(
            "Query could not be executed safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        QueryExecutionUnavailableError,
        TextToSQLUnavailableError,
    ):
        return _error_response(
            "Query execution is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except Exception:
        return _error_response(
            "Query execution is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    headers = {
        **NO_STORE_HEADERS,
        "X-Query-Execution-Version": (result.execution_version),
        "X-SQL-Generation-Version": (result.generation.generation_version),
        "X-SQL-Validation-Version": (result.generation.validated_sql.validation_version),
        "X-Catalog-Version": (result.generation.validated_sql.catalog_version),
        "X-Semantic-Version": (result.generation.validated_sql.semantic_version),
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(mode="json"),
        headers=headers,
    )
