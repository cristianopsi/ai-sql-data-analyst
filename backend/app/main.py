from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import __version__
from backend.app.api.analytics import router as analytics_router
from backend.app.api.catalog import router as catalog_router
from backend.app.api.grounding import router as grounding_router
from backend.app.api.health import router as health_router
from backend.app.api.insights import router as insights_router
from backend.app.api.presentation import router as presentation_router
from backend.app.api.query_execution import (
    router as query_execution_router,
)
from backend.app.api.sql_generation import (
    router as sql_generation_router,
)
from backend.app.api.visualization import router as visualization_router
from backend.app.core.auth import AuthConfig, AuthMiddleware
from backend.app.core.config import Settings, get_settings
from backend.app.core.lifecycle import (
    DatabasePoolFactory,
    PresentationServiceFactory,
    create_database_lifespan,
)
from backend.app.core.observability import ObservabilityMiddleware, configure_logger
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
from backend.app.services.insight_engine import (
    InsightEngineFactory,
    create_insight_engine,
)
from backend.app.services.llm_provider import (
    LLMProviderFactory,
    create_llm_provider,
)
from backend.app.services.presentation_service import create_presentation_service
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
from backend.app.services.visualization_engine import (
    VisualizationEngineFactory,
    create_visualization_engine,
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
    visualization_engine_factory: VisualizationEngineFactory = (create_visualization_engine),
    insight_engine_factory: InsightEngineFactory = create_insight_engine,
    presentation_service_factory: PresentationServiceFactory = (create_presentation_service),
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
            visualization_engine_factory,
            insight_engine_factory,
            presentation_service_factory,
        ),
    )

    application.state.settings = resolved_settings
    application.state.database_ready = False

    configure_logger(
        log_level=resolved_settings.log_level,
        log_format=resolved_settings.log_format,
    )
    application.add_middleware(ObservabilityMiddleware)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    auth_config = AuthConfig(
        enabled=resolved_settings.auth_enabled,
        issuer=resolved_settings.oidc_issuer,
        audience=resolved_settings.oidc_audience,
        jwks_url=resolved_settings.oidc_jwks_url,
        jwks_cache_ttl_seconds=resolved_settings.oidc_jwks_cache_ttl_seconds,
    )
    application.add_middleware(AuthMiddleware, config=auth_config)

    application.include_router(health_router)
    application.include_router(catalog_router)
    application.include_router(grounding_router)
    application.include_router(sql_generation_router)
    application.include_router(query_execution_router)
    application.include_router(analytics_router)
    application.include_router(visualization_router)
    application.include_router(insights_router)
    application.include_router(presentation_router)

    return application


app = create_app()
