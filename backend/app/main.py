from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import __version__
from backend.app.api.catalog import router as catalog_router
from backend.app.api.health import router as health_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.lifecycle import (
    DatabasePoolFactory,
    create_database_lifespan,
)
from backend.app.db.pools import create_database_pools
from backend.app.services.catalog_cache import (
    CatalogCacheFactory,
    create_schema_catalog_cache,
)


def create_app(
    *,
    settings: Settings | None = None,
    pool_factory: DatabasePoolFactory = (create_database_pools),
    catalog_cache_factory: CatalogCacheFactory = (create_schema_catalog_cache),
) -> FastAPI:
    resolved_settings = settings or get_settings()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.app_debug,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=create_database_lifespan(
            resolved_settings,
            pool_factory,
            catalog_cache_factory,
        ),
    )

    application.state.settings = resolved_settings
    application.state.database_ready = False

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    application.include_router(health_router)
    application.include_router(catalog_router)

    return application


app = create_app()
