from typing import Annotated, cast

from fastapi import (
    APIRouter,
    Depends,
    Request,
    status,
)
from fastapi.responses import JSONResponse

from backend.app import __version__
from backend.app.core.config import Settings, get_settings
from backend.app.db.pools import (
    DatabasePools,
    RuntimeConnectionPool,
)
from backend.app.schemas.health import (
    DatabaseReadiness,
    HealthResponse,
    ReadinessResponse,
)

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


def check_database_pool(
    pool: RuntimeConnectionPool,
    *,
    expected_read_only: str,
) -> bool:
    """Check connectivity and the expected transaction mode."""
    try:
        with pool.connection() as connection:
            row = connection.execute(
                """
                SELECT current_setting(
                    'transaction_read_only'
                )
                """
            ).fetchone()
    except Exception:
        return False

    return row is not None and str(row[0]) == expected_read_only


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": ("One or more required database pools are unavailable."),
        }
    },
    summary="Check API readiness",
)
def readiness(request: Request) -> JSONResponse:
    application_database_ready = False
    analytics_database_ready = False

    lifecycle_ready = bool(
        getattr(
            request.app.state,
            "database_ready",
            False,
        )
    )
    pools_value = getattr(
        request.app.state,
        "database_pools",
        None,
    )

    if lifecycle_ready and pools_value is not None:
        pools = cast(DatabasePools, pools_value)

        application_database_ready = check_database_pool(
            pools.application,
            expected_read_only="off",
        )
        analytics_database_ready = check_database_pool(
            pools.analytics,
            expected_read_only="on",
        )

    fully_ready = application_database_ready and analytics_database_ready

    response = ReadinessResponse(
        status=("ready" if fully_ready else "not_ready"),
        application_database=DatabaseReadiness(
            status=("ok" if application_database_ready else "unavailable")
        ),
        analytics_database=DatabaseReadiness(
            status=("ok" if analytics_database_ready else "unavailable")
        ),
    )

    response_status = status.HTTP_200_OK if fully_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=response_status,
        content=response.model_dump(mode="json"),
    )
