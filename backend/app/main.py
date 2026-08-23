from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import __version__
from backend.app.api.health import router as health_router
from backend.app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.app_debug,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    application.include_router(health_router)

    return application


app = create_app()
