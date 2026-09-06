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

from backend.app.schemas.analytics import (
    AnalyticsApiErrorDetail,
    AnalyticsApiErrorResponse,
    AnalyticsRequest,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
)
from backend.app.services.analytics_engine import (
    AnalyticsInputError,
    DeterministicAnalyticsEngine,
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
    detail: AnalyticsApiErrorDetail,
    status_code: int,
) -> JSONResponse:
    response = AnalyticsApiErrorResponse(
        detail=detail,
    )

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers=NO_STORE_HEADERS,
    )


class SanitizedAnalyticsRoute(APIRoute):
    """Sanitize analytics request-contract validation failures."""

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
    prefix="/api/v1/analytics",
    tags=["analytics"],
    route_class=SanitizedAnalyticsRoute,
)


def _generate_execute_analyze(
    pipeline: SQLGenerationPipeline,
    executor: QueryExecutor,
    engine: DeterministicAnalyticsEngine,
    question: str,
) -> DeterministicAnalyticsResult:
    generation = pipeline.generate(question)
    context = generation.internal_context

    if context is None:
        raise AnalyticsInputError("Validated generation context is unavailable")

    execution: QueryExecutionResult = executor.execute(generation)

    return engine.analyze(
        execution,
        context,
    )


@router.post(
    "/analyze",
    response_model=DeterministicAnalyticsResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": AnalyticsApiErrorResponse,
            "description": (
                "The question, generated SQL, query result, or analytics "
                "could not pass controlled validation."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": AnalyticsApiErrorResponse,
            "description": (
                "The managed generation, execution, or analytics service is unavailable."
            ),
        },
    },
    summary="Generate, execute, and analyze a governed question",
)
async def analyze_question(
    payload: AnalyticsRequest,
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
    engine_value = getattr(
        request.app.state,
        "analytics_engine",
        None,
    )
    database_ready = getattr(
        request.app.state,
        "database_ready",
        False,
    )

    if (
        pipeline_value is None
        or executor_value is None
        or engine_value is None
        or database_ready is not True
    ):
        return _error_response(
            "Analytics service is unavailable",
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
    engine = cast(
        DeterministicAnalyticsEngine,
        engine_value,
    )

    try:
        result = await run_in_threadpool(
            _generate_execute_analyze,
            pipeline,
            executor,
            engine,
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
            "Analytics could not be produced safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        AnalyticsInputError,
        QueryExecutionResultError,
        QueryExecutionSecurityError,
    ):
        return _error_response(
            "Analytics could not be produced safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        QueryExecutionUnavailableError,
        TextToSQLUnavailableError,
    ):
        return _error_response(
            "Analytics service is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except Exception:
        return _error_response(
            "Analytics service is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    headers = {
        **NO_STORE_HEADERS,
        "X-Analytics-Version": result.analytics_version,
        "X-Query-Execution-Version": result.execution_version,
        "X-Catalog-Version": result.catalog_version,
        "X-Semantic-Version": result.semantic_version,
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(mode="json"),
        headers=headers,
    )
