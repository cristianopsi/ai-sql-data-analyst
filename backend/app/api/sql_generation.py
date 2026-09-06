from typing import cast

from fastapi import (
    APIRouter,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.sql_generation import (
    SQLGenerationApiErrorDetail,
    SQLGenerationApiErrorResponse,
    SQLGenerationRequest,
    SQLGenerationResult,
)
from backend.app.services.grounding_context import (
    GroundingContextError,
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

router = APIRouter(
    prefix="/api/v1/sql",
    tags=["sql-generation"],
)

NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
}


def _error_response(
    detail: SQLGenerationApiErrorDetail,
    status_code: int,
) -> JSONResponse:
    response = SQLGenerationApiErrorResponse(detail=detail)

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers=NO_STORE_HEADERS,
    )


@router.post(
    "/generate",
    response_model=SQLGenerationResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": SQLGenerationApiErrorResponse,
            "description": ("The question or generated SQL could not pass controlled validation."),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": SQLGenerationApiErrorResponse,
            "description": ("The managed SQL generation pipeline is unavailable."),
        },
    },
    summary="Generate validated read-only SQL",
)
async def generate_sql(
    payload: SQLGenerationRequest,
    request: Request,
) -> Response:
    pipeline_value = getattr(
        request.app.state,
        "sql_generation_pipeline",
        None,
    )

    if pipeline_value is None:
        return _error_response(
            "SQL generation is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    pipeline = cast(
        SQLGenerationPipeline,
        pipeline_value,
    )

    try:
        result = await run_in_threadpool(
            pipeline.generate,
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
    except TextToSQLGroundingError:
        return _error_response(
            "SQL could not be generated safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except (
        SQLGenerationExhaustedError,
        TextToSQLResponseError,
    ):
        return _error_response(
            "SQL could not be generated safely",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except TextToSQLUnavailableError:
        return _error_response(
            "SQL generation is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except Exception:
        return _error_response(
            "SQL generation is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    headers = {
        **NO_STORE_HEADERS,
        "X-SQL-Generation-Version": (result.generation_version),
        "X-SQL-Validation-Version": (result.validated_sql.validation_version),
        "X-Catalog-Version": (result.validated_sql.catalog_version),
        "X-Semantic-Version": (result.validated_sql.semantic_version),
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result.model_dump(mode="json"),
        headers=headers,
    )
