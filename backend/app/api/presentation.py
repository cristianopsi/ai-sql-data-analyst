"""Presentation API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.presentation import (
    PresentationGenerateRequest,
    PresentationGenerateResponse,
)
from backend.app.schemas.presentation_artifact import (
    PresentationExportRequest,
    PresentationExportResponse,
)
from backend.app.services.presentation_artifact_service import (
    PresentationArtifactService,
)
from backend.app.services.presentation_service import PresentationService

router = APIRouter(prefix="/api/v1", tags=["presentation"])


@router.post(
    "/presentations/generate",
    response_model=PresentationGenerateResponse,
    dependencies=[Depends(require_roles({Role.PAID_USER, Role.ADMIN}))],
)
async def generate_presentation(
    request: PresentationGenerateRequest,
) -> PresentationGenerateResponse:
    """Generate an executive presentation from analytics results."""
    service = PresentationService()
    return service.generate(request)


@router.post(
    "/presentations/export",
    response_model=PresentationExportResponse,
    dependencies=[Depends(require_roles({Role.PAID_USER, Role.ADMIN}))],
)
async def export_presentation(
    request: PresentationExportRequest,
) -> PresentationExportResponse:
    """Export a presentation as a downloadable artifact."""
    service = PresentationArtifactService()
    return service.export(request)
