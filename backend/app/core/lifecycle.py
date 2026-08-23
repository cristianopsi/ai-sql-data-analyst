from collections.abc import AsyncIterator, Callable
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
from typing import Protocol

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import Settings

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
) -> FastAPILifespan:
    """Create a FastAPI lifespan that owns the database pools."""

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        application.state.database_ready = False

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
