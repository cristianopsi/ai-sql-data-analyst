"""Combined analytical presentation HTTP API."""

from collections.abc import Callable, Coroutine
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationApiErrorResponse,
    PresentationRequest,
)
from backend.app.schemas.presentation_artifact import (
    PPTX_MIME_TYPE,
    PresentationArtifact,
)
from backend.app.services.analytics_engine import AnalyticsEngineError
from backend.app.services.grounding_context import GroundingContextError
from backend.app.services.insight_engine import InsightEngineError
from backend.app.services.llm_provider import LLMProviderUnavailableError
from backend.app.services.presentation_artifact_service import (
    PresentationArtifactError,
    PresentationArtifactService,
)
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


def _artifact_response(
    artifact: PresentationArtifact,
) -> StreamingResponse:
    headers = {
        **NO_STORE_HEADERS,
        "Content-Disposition": (f'attachment; filename="{artifact.filename}"'),
        "Content-Length": str(artifact.size_bytes),
        "X-Content-Type-Options": "nosniff",
        "X-Presentation-Artifact-Id": artifact.artifact_id,
        "X-Presentation-Artifact-Version": artifact.artifact_version,
        "X-Presentation-Version": artifact.presentation_version,
        "X-Insight-Version": artifact.insight_version,
        "X-Visualization-Version": artifact.visualization_version,
        "X-Analytics-Version": artifact.analytics_version,
        "X-Execution-Version": artifact.execution_version,
    }

    return StreamingResponse(
        BytesIO(artifact.content),
        status_code=status.HTTP_200_OK,
        media_type=artifact.media_type,
        headers=headers,
    )


async def _generate_result(
    payload: PresentationRequest,
    request: Request,
) -> AnalyticalPresentationResult | JSONResponse:
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
        return await run_in_threadpool(
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
    result = await _generate_result(payload, request)

    if isinstance(result, JSONResponse):
        return result

    return _success_response(result)


@router.post(
    "/export",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "content": {PPTX_MIME_TYPE: {}},
            "description": ("One validated in-memory PowerPoint presentation artifact."),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": PresentationApiErrorResponse,
            "description": (
                "The presentation request, pipeline result, or artifact could not pass validation."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": PresentationApiErrorResponse,
            "description": ("The managed analytical presentation service is unavailable."),
        },
    },
    summary="Export one analytical presentation as PowerPoint",
)
async def export_presentation(
    payload: PresentationRequest,
    request: Request,
) -> Response:
    artifact_service_value = getattr(
        request.app.state,
        "presentation_artifact_service",
        None,
    )

    if not isinstance(
        artifact_service_value,
        PresentationArtifactService,
    ):
        return _error_response(
            UNAVAILABLE_DETAIL,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    result = await _generate_result(payload, request)

    if isinstance(result, JSONResponse):
        return result

    try:
        artifact = await run_in_threadpool(
            artifact_service_value.build_pptx,
            result,
        )
    except PresentationArtifactError:
        return _error_response(
            CONTROLLED_FAILURE_DETAIL,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except Exception:
        return _error_response(
            UNAVAILABLE_DETAIL,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return _artifact_response(artifact)
