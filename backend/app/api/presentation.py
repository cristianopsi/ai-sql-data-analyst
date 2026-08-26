"""Combined analytical presentation HTTP API."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationApiErrorResponse,
    PresentationRequest,
)
from backend.app.services.analytics_engine import AnalyticsEngineError
from backend.app.services.grounding_context import GroundingContextError
from backend.app.services.insight_engine import InsightEngineError
from backend.app.services.llm_provider import LLMProviderUnavailableError
from backend.app.services.presentation_service import (
    AnalyticalPresentationService,
    PresentationServiceError,
)
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
)
from backend.app.services.question_grounding import QuestionGroundingError
from backend.app.services.sql_generation import SQLGenerationExhaustedError
from backend.app.services.text_to_sql import (
    TextToSQLGroundingError,
    TextToSQLResponseError,
    TextToSQLUnavailableError,
)
from backend.app.services.visualization_engine import VisualizationEngineError

INVALID_REQUEST_DETAIL = "Presentation request is invalid"
CONTROLLED_FAILURE_DETAIL = "Presentation could not be produced safely"
UNAVAILABLE_DETAIL = "Presentation service is unavailable"
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def _error_response(
    detail: str,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=NO_STORE_HEADERS,
    )


class SanitizedPresentationRoute(APIRoute):
    """Convert request validation failures into a bounded API response."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def sanitized_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError:
                return _error_response(
                    INVALID_REQUEST_DETAIL,
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

        return sanitized_handler


router = APIRouter(
    prefix="/api/v1/presentations",
    tags=["presentations"],
    route_class=SanitizedPresentationRoute,
)


def _success_response(
    result: AnalyticalPresentationResult,
) -> JSONResponse:
    headers = {
        **NO_STORE_HEADERS,
        "X-Presentation-Version": str(result.presentation_version),
        "X-Insight-Version": str(result.insights.insight_version),
        "X-Visualization-Version": str(result.visualizations.visualization_version),
        "X-Analytics-Version": str(result.visualizations.analytics_version),
        "X-Execution-Version": str(result.visualizations.execution_version),
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(mode="json"),
        headers=headers,
    )


@router.post(
    "/generate",
    response_model=AnalyticalPresentationResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": PresentationApiErrorResponse,
            "description": (
                "The presentation request or a controlled pipeline result "
                "could not pass validation."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": PresentationApiErrorResponse,
            "description": ("The managed analytical presentation service is unavailable."),
        },
    },
    summary="Generate one complete analytical presentation",
)
async def generate_presentation(
    payload: PresentationRequest,
    request: Request,
) -> Response:
    database_ready = getattr(
        request.app.state,
        "database_ready",
        False,
    )
    service_value = getattr(
        request.app.state,
        "presentation_service",
        None,
    )

    if database_ready is not True or not isinstance(
        service_value,
        AnalyticalPresentationService,
    ):
        return _error_response(
            UNAVAILABLE_DETAIL,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        result = await run_in_threadpool(
            service_value.generate,
            payload.question,
        )
    except (
        GroundingContextError,
        QuestionGroundingError,
    ):
        return _error_response(
            INVALID_REQUEST_DETAIL,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        PresentationServiceError,
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
