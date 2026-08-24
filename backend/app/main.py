from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import __version__
from backend.app.api.analytics import router as analytics_router
from backend.app.api.catalog import router as catalog_router
from backend.app.api.grounding import router as grounding_router
from backend.app.api.health import router as health_router
from backend.app.api.query_execution import (
    router as query_execution_router,
)
from backend.app.api.sql_generation import (
    router as sql_generation_router,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.lifecycle import (
    DatabasePoolFactory,
    create_database_lifespan,
)
from backend.app.db.pools import create_database_pools
from backend.app.services.analytics_engine import (
    AnalyticsEngineFactory,
    create_analytics_engine,
)
from backend.app.services.catalog_cache import (
    CatalogCacheFactory,
    create_schema_catalog_cache,
)
from backend.app.services.grounding_context import (
    GroundingContextServiceFactory,
    create_grounding_context_service,
)
from backend.app.services.llm_provider import (
    LLMProviderFactory,
    create_llm_provider,
)
from backend.app.services.query_executor import (
    QueryExecutorFactory,
    create_query_executor,
)
from backend.app.services.sql_generation import (
    SQLGenerationPipelineFactory,
    create_sql_generation_pipeline,
)
from backend.app.services.sql_validator import (
    SQLValidatorFactory,
    create_sql_validator,
)
from backend.app.services.text_to_sql import (
    TextToSQLServiceFactory,
    create_text_to_sql_service,
)


def create_app(
    *,
    settings: Settings | None = None,
    pool_factory: DatabasePoolFactory = (create_database_pools),
    catalog_cache_factory: CatalogCacheFactory = (create_schema_catalog_cache),
    grounding_context_service_factory: GroundingContextServiceFactory = (
        create_grounding_context_service
    ),
    llm_provider_factory: LLMProviderFactory = (create_llm_provider),
    text_to_sql_service_factory: TextToSQLServiceFactory = (create_text_to_sql_service),
    sql_validator_factory: SQLValidatorFactory = (create_sql_validator),
    sql_generation_pipeline_factory: SQLGenerationPipelineFactory = (
        create_sql_generation_pipeline
    ),
    query_executor_factory: QueryExecutorFactory = (create_query_executor),
    analytics_engine_factory: AnalyticsEngineFactory = (create_analytics_engine),
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
            grounding_context_service_factory,
            llm_provider_factory,
            text_to_sql_service_factory,
            sql_validator_factory,
            sql_generation_pipeline_factory,
            query_executor_factory,
            analytics_engine_factory,
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
    application.include_router(grounding_router)
    application.include_router(sql_generation_router)
    application.include_router(query_execution_router)
    application.include_router(analytics_router)

    return application


app = create_app()
