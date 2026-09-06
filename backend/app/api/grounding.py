from typing import cast

from fastapi import (
    APIRouter,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from backend.app.schemas.grounding import (
    GroundingApiErrorDetail,
    GroundingApiErrorResponse,
    GroundingContextRequest,
)
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.services.grounding_context import (
    GroundingContextService,
)
from backend.app.services.question_grounding import (
    QuestionGroundingError,
)

router = APIRouter(
    prefix="/api/v1/grounding",
    tags=["grounding"],
)


def _error_response(
    detail: GroundingApiErrorDetail,
    *,
    status_code: int,
) -> JSONResponse:
    response = GroundingApiErrorResponse(detail=detail)

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers={
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/context",
    response_model=CompactGroundingContext,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": GroundingApiErrorResponse,
            "description": ("The question requests restricted data."),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": GroundingApiErrorResponse,
            "description": ("The question is invalid or unsupported."),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": GroundingApiErrorResponse,
            "description": ("The grounding service is unavailable."),
        },
    },
    summary="Build safe semantic context for a question",
)
def create_grounding_context(
    payload: GroundingContextRequest,
    request: Request,
) -> Response:
    service_value = getattr(
        request.app.state,
        "grounding_context_service",
        None,
    )

    if service_value is None:
        return _error_response(
            "Grounding context is unavailable",
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
        )

    service = cast(
        GroundingContextService,
        service_value,
    )

    try:
        context = service.build(payload.question)
    except QuestionGroundingError:
        return _error_response(
            "Question is invalid",
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
        )
    except Exception:
        return _error_response(
            "Grounding context is unavailable",
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
        )

    if context.grounding_status == "restricted":
        return _error_response(
            "Question cannot be grounded safely",
            status_code=(status.HTTP_403_FORBIDDEN),
        )

    if context.grounding_status == "unsupported":
        return _error_response(
            ("Question is outside the supported analytics domain"),
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=context.model_dump(mode="json"),
        headers={
            "Cache-Control": "no-store",
            "X-Grounding-Status": (context.grounding_status),
            "X-Semantic-Version": (context.semantic_version),
            "X-Catalog-Version": (context.catalog_version),
        },
    )
