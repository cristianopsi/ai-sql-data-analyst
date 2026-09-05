"""SQL generation API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.sql_generation import (
    SQLGenerationRequest,
    SQLGenerationResponse,
)
from backend.app.services.sql_generation import SQLGenerationService

router = APIRouter(prefix="/api/v1", tags=["sql-generation"])


@router.post(
    "/sql/generate",
    response_model=SQLGenerationResponse,
    dependencies=[Depends(require_roles({Role.FREE_USER, Role.PAID_USER, Role.ADMIN}))],
)
async def generate_sql(request: SQLGenerationRequest) -> SQLGenerationResponse:
    """Generate SQL from a grounded question."""
    service = SQLGenerationService()
    return service.generate(request)
