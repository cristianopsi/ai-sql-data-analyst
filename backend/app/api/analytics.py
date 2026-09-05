"""Analytics API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.analytics import AnalyticsRequest, AnalyticsResponse
from backend.app.services.analytics_engine import AnalyticsEngineService

router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.post(
    "/analytics",
    response_model=AnalyticsResponse,
    dependencies=[Depends(require_roles({Role.PAID_USER, Role.ADMIN}))],
)
async def analyze(request: AnalyticsRequest) -> AnalyticsResponse:
    """Run deterministic analytics on query results."""
    service = AnalyticsEngineService()
    return service.analyze(request)
