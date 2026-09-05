"""Visualization API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.visualization import (
    VisualizationRequest,
    VisualizationResponse,
)
from backend.app.services.visualization_engine import VisualizationEngineService

router = APIRouter(prefix="/api/v1", tags=["visualization"])


@router.post(
    "/visualizations",
    response_model=VisualizationResponse,
    dependencies=[Depends(require_roles({Role.PAID_USER, Role.ADMIN}))],
)
async def create_visualization(request: VisualizationRequest) -> VisualizationResponse:
    """Generate chart specifications from analytics results."""
    service = VisualizationEngineService()
    return service.visualize(request)
