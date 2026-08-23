from collections.abc import Callable
from threading import RLock
from time import monotonic

from backend.app.core.config import Settings
from backend.app.schemas.catalog import SchemaCatalog
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)

type CatalogBuilder = Callable[[], SchemaCatalog]
type MonotonicClock = Callable[[], float]


class SchemaCatalogCache:
    """Thread-safe in-memory cache for the schema catalog."""

    def __init__(
        self,
        builder: CatalogBuilder,
        ttl_seconds: float,
        *,
        clock: MonotonicClock = monotonic,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds cannot be negative")

        self._builder = builder
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = RLock()
        self._catalog: SchemaCatalog | None = None
        self._expires_at = 0.0
        self._generation = 0

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def is_cached(self) -> bool:
        with self._lock:
            return self._catalog is not None and self._clock() < self._expires_at

    def get(self) -> SchemaCatalog:
        """Return a valid cached catalog or build a new one."""
        with self._lock:
            now = self._clock()

            if self._catalog is not None and now < self._expires_at:
                return self._catalog

            catalog = self._builder()

            self._catalog = catalog
            self._expires_at = now + self._ttl_seconds
            self._generation += 1

            return catalog

    def invalidate(self) -> None:
        """Discard the cached catalog."""
        with self._lock:
            self._catalog = None
            self._expires_at = 0.0


type CatalogCacheFactory = Callable[
    [Settings],
    SchemaCatalogCache,
]


def create_schema_catalog_cache(
    settings: Settings,
) -> SchemaCatalogCache:
    """Create the application schema-catalog cache."""
    return SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=(settings.schema_cache_ttl_seconds),
    )
