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

        pools = pool_factory(settings)
        application.state.database_pools = pools

        pools_opened = False

        try:
            await run_in_threadpool(pools.open)
            pools_opened = True
            application.state.database_ready = True

            yield
        finally:
            application.state.database_ready = False

            if pools_opened:
                await run_in_threadpool(pools.close)

    return lifespan
