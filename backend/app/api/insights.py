"""Controlled grounded insight generation API."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.analytics import DeterministicAnalyticsResult
from backend.app.schemas.insights import (
    GroundedInsightRequest,
    GroundedInsightResult,
)
from backend.app.schemas.query_execution import QueryExecutionResult
from backend.app.schemas.visualization import DeterministicVisualizationResult
from backend.app.services.analytics_engine import (
    AnalyticsEngineError,
    AnalyticsInputError,
    DeterministicAnalyticsEngine,
)
from backend.app.services.grounding_context import GroundingContextError
from backend.app.services.insight_engine import (
    GroundedInsightEngine,
    InsightEngineError,
)
from backend.app.services.llm_provider import LLMProviderUnavailableError
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
    QueryExecutor,
)
from backend.app.services.question_grounding import QuestionGroundingError
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

NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
}

INVALID_QUESTION_DETAIL = "Question is invalid"
CONTROLLED_FAILURE_DETAIL = "Insight could not be produced safely"
UNAVAILABLE_DETAIL = "Insight service is unavailable"


def _error_response(
    detail: str,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=NO_STORE_HEADERS,
    )


class SanitizedInsightRoute(APIRoute):
    """Convert request validation details into a stable public error."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_route_handler = super().get_route_handler()

        async def sanitized_route_handler(
            request: Request,
        ) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError:
                return _error_response(
                    INVALID_QUESTION_DETAIL,
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

        return sanitized_route_handler


router = APIRouter(
    prefix="/api/v1/insights",
    tags=["insights"],
    route_class=SanitizedInsightRoute,
)


def _generate_execute_analyze_specify_explain(
    pipeline: SQLGenerationPipeline,
    executor: QueryExecutor,
    analytics_engine: DeterministicAnalyticsEngine,
    visualization_engine: DeterministicVisualizationEngine,
    insight_engine: GroundedInsightEngine,
    question: str,
) -> GroundedInsightResult:
    generation = pipeline.generate(question)
    context = generation.internal_context

    if context is None:
        raise AnalyticsInputError("Validated generation context is unavailable")

    execution: QueryExecutionResult = executor.execute(generation)
    analytics: DeterministicAnalyticsResult = analytics_engine.analyze(
        execution,
        context,
    )
    visualizations: DeterministicVisualizationResult = visualization_engine.specify(analytics)

    return insight_engine.generate(
        analytics,
        visualizations,
    )


def _success_response(
    result: GroundedInsightResult,
) -> JSONResponse:
    headers = {
        **NO_STORE_HEADERS,
        "X-Insight-Version": str(result.insight_version),
        "X-Visualization-Version": str(result.visualization_version),
        "X-Analytics-Version": str(result.analytics_version),
        "X-Execution-Version": str(result.execution_version),
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(mode="json"),
        headers=headers,
    )


@router.post(
    "/generate",
    response_model=GroundedInsightResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The question, generated SQL, query result, analytics, "
                "visualizations, or narrative could not pass controlled "
                "validation."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": (
                "A managed generation, execution, analytics, "
                "visualization, or insight service is unavailable."
            ),
        },
    },
    summary=("Generate grounded insights for a governed natural-language question"),
)
async def generate_insights(
    payload: GroundedInsightRequest,
    request: Request,
) -> Response:
    database_ready = getattr(
        request.app.state,
        "database_ready",
        False,
    )
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
    insight_engine_value = getattr(
        request.app.state,
        "insight_engine",
        None,
    )

    if (
        database_ready is not True
        or not isinstance(
            pipeline_value,
            SQLGenerationPipeline,
        )
        or not isinstance(
            executor_value,
            QueryExecutor,
        )
        or not isinstance(
            analytics_engine_value,
            DeterministicAnalyticsEngine,
        )
        or not isinstance(
            visualization_engine_value,
            DeterministicVisualizationEngine,
        )
        or not isinstance(
            insight_engine_value,
            GroundedInsightEngine,
        )
    ):
        return _error_response(
            UNAVAILABLE_DETAIL,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        result = await run_in_threadpool(
            _generate_execute_analyze_specify_explain,
            pipeline_value,
            executor_value,
            analytics_engine_value,
            visualization_engine_value,
            insight_engine_value,
            payload.question,
        )
    except (
        GroundingContextError,
        QuestionGroundingError,
    ):
        return _error_response(
            INVALID_QUESTION_DETAIL,
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
        InsightEngineError,
    ):
        return _error_response(
            CONTROLLED_FAILURE_DETAIL,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        TextToSQLUnavailableError,
        QueryExecutionUnavailableError,
        LLMProviderUnavailableError,
    ):
        return _error_response(
            UNAVAILABLE_DETAIL,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except Exception:
        return _error_response(
            UNAVAILABLE_DETAIL,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return _success_response(result)
