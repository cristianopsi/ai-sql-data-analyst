from typing import Annotated

from fastapi import APIRouter, Depends, status

from backend.app import __version__
from backend.app.core.config import Settings, get_settings
from backend.app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check API liveness",
)
def health(settings: SettingsDependency) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
    )
