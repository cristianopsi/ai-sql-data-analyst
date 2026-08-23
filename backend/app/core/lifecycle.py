from collections.abc import AsyncIterator, Callable
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
from typing import Protocol

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import Settings
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

type FastAPILifespan = Callable[
    [FastAPI],
    AbstractAsyncContextManager[None],
]


class DatabasePoolLifecycle(Protocol):
    def open(self) -> None:
        """Open and validate every required database pool."""

    def close(self) -> None:
        """Close every database pool."""


type DatabasePoolFactory = Callable[
    [Settings],
    DatabasePoolLifecycle,
]


def create_database_lifespan(
    settings: Settings,
    pool_factory: DatabasePoolFactory,
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
) -> FastAPILifespan:
    """Create a FastAPI lifespan that owns the database pools."""

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        application.state.database_ready = False

        schema_catalog_cache = catalog_cache_factory(settings)
        application.state.schema_catalog_cache = schema_catalog_cache

        grounding_context_service = grounding_context_service_factory(
            settings,
            schema_catalog_cache,
        )
        application.state.grounding_context_service = grounding_context_service

        llm_provider = llm_provider_factory(settings)
        application.state.llm_provider = llm_provider

        pools: DatabasePoolLifecycle | None = None
        pools_opened = False

        try:
            text_to_sql_service = text_to_sql_service_factory(
                settings,
                llm_provider,
                grounding_context_service,
            )
            application.state.text_to_sql_service = text_to_sql_service

            sql_validator = sql_validator_factory(settings)
            application.state.sql_validator = sql_validator

            sql_generation_pipeline = sql_generation_pipeline_factory(
                settings,
                grounding_context_service,
                text_to_sql_service,
                sql_validator,
            )
            application.state.sql_generation_pipeline = sql_generation_pipeline

            pools = pool_factory(settings)
            application.state.database_pools = pools

            await run_in_threadpool(pools.open)
            pools_opened = True
            application.state.database_ready = True

            yield
        finally:
            application.state.database_ready = False

            try:
                if pools_opened and pools is not None:
                    await run_in_threadpool(pools.close)
            finally:
                await run_in_threadpool(llm_provider.close)

    return lifespan
