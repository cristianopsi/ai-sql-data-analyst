"""Insights API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.insights import InsightsRequest, InsightsResponse
from backend.app.services.insight_engine import InsightEngineService

router = APIRouter(prefix="/api/v1", tags=["insights"])


@router.post(
    "/insights",
    response_model=InsightsResponse,
    dependencies=[Depends(require_roles({Role.PAID_USER, Role.ADMIN}))],
)
async def generate_insights(request: InsightsRequest) -> InsightsResponse:
    """Generate contextual insights from analytics results."""
    service = InsightEngineService()
    return service.explain(request)
