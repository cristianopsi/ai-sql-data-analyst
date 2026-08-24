from collections.abc import (
    Callable,
    Coroutine,
)
from typing import (
    Any,
    cast,
)

from fastapi import (
    APIRouter,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.analytics import (
    DeterministicAnalyticsResult,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
)
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
    VisualizationApiErrorDetail,
    VisualizationApiErrorResponse,
    VisualizationRequest,
)
from backend.app.services.analytics_engine import (
    AnalyticsEngineError,
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
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
    VisualizationEngineError,
)

type RouteHandler = Callable[
    [Request],
    Coroutine[Any, Any, Response],
]

NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
}


def _error_response(
    detail: VisualizationApiErrorDetail,
    status_code: int,
) -> JSONResponse:
    response = VisualizationApiErrorResponse(
        detail=detail,
    )

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(
            mode="json",
        ),
        headers=NO_STORE_HEADERS,
    )


class SanitizedVisualizationRoute(APIRoute):
    """Normalize request validation failures without leaking details."""

    def get_route_handler(self) -> RouteHandler:
        original_handler = super().get_route_handler()

        async def sanitized_handler(
            request: Request,
        ) -> Response:
            try:
                response = await original_handler(request)
            except RequestValidationError:
                response = _error_response(
                    "Question is invalid",
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

            response.headers.update(
                NO_STORE_HEADERS,
            )

            return response

        return sanitized_handler


router = APIRouter(
    prefix="/api/v1/visualizations",
    tags=["visualizations"],
    route_class=SanitizedVisualizationRoute,
)


def _generate_execute_analyze_specify(
    pipeline: SQLGenerationPipeline,
    executor: QueryExecutor,
    analytics_engine: DeterministicAnalyticsEngine,
    visualization_engine: DeterministicVisualizationEngine,
    question: str,
) -> DeterministicVisualizationResult:
    generation = pipeline.generate(question)
    context = generation.internal_context

    if context is None:
        raise AnalyticsInputError("Validated generation context is unavailable")

    execution: QueryExecutionResult = executor.execute(generation)
    analytics: DeterministicAnalyticsResult = analytics_engine.analyze(
        execution,
        context,
    )

    return visualization_engine.specify(analytics)


@router.post(
    "/specify",
    response_model=DeterministicVisualizationResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": VisualizationApiErrorResponse,
            "description": (
                "The question, generated SQL, query result, "
                "analytics, or visualization could not pass "
                "controlled validation."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": VisualizationApiErrorResponse,
            "description": (
                "The managed generation, execution, analytics, "
                "or visualization service is unavailable."
            ),
        },
    },
    summary=("Generate deterministic visualization specifications for a governed question"),
)
async def specify_visualizations(
    payload: VisualizationRequest,
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
    analytics_engine_value = getattr(
        request.app.state,
        "analytics_engine",
        None,
    )
    visualization_engine_value = getattr(
        request.app.state,
        "visualization_engine",
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
        or analytics_engine_value is None
        or visualization_engine_value is None
        or database_ready is not True
    ):
        return _error_response(
            "Visualization service is unavailable",
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
    analytics_engine = cast(
        DeterministicAnalyticsEngine,
        analytics_engine_value,
    )
    visualization_engine = cast(
        DeterministicVisualizationEngine,
        visualization_engine_value,
    )

    try:
        result = await run_in_threadpool(
            _generate_execute_analyze_specify,
            pipeline,
            executor,
            analytics_engine,
            visualization_engine,
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
        QueryExecutionResultError,
        QueryExecutionSecurityError,
        AnalyticsEngineError,
        VisualizationEngineError,
    ):
        return _error_response(
            "Visualization could not be produced safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        TextToSQLUnavailableError,
        QueryExecutionUnavailableError,
    ):
        return _error_response(
            "Visualization service is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    headers = {
        **NO_STORE_HEADERS,
        "X-Visualization-Version": (result.visualization_version),
        "X-Analytics-Version": result.analytics_version,
        "X-Execution-Version": result.execution_version,
        "X-Semantic-Version": result.semantic_version,
        "X-Catalog-Version": result.catalog_version,
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(
            mode="json",
        ),
        headers=headers,
    )
