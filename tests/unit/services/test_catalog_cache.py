from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event

import pytest

from backend.app.core.config import Settings
from backend.app.schemas.catalog import SchemaCatalog
from backend.app.services.catalog_cache import (
    SchemaCatalogCache,
    create_schema_catalog_cache,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)


@dataclass(slots=True)
class MutableClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_negative_ttl_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="ttl_seconds cannot be negative",
    ):
        SchemaCatalogCache(
            build_schema_catalog,
            ttl_seconds=-1,
        )


def test_catalog_is_reused_before_expiration() -> None:
    clock = MutableClock()
    builds: list[SchemaCatalog] = []

    def builder() -> SchemaCatalog:
        catalog = build_schema_catalog()
        builds.append(catalog)

        return catalog

    cache = SchemaCatalogCache(
        builder,
        ttl_seconds=10,
        clock=clock,
    )

    first = cache.get()
    clock.advance(9)
    second = cache.get()

    assert first is second
    assert builds == [first]
    assert cache.generation == 1
    assert cache.is_cached is True


def test_catalog_is_refreshed_at_expiration() -> None:
    clock = MutableClock()
    build_count = 0

    def builder() -> SchemaCatalog:
        nonlocal build_count
        build_count += 1

        return build_schema_catalog().model_copy(
            update={
                "catalog_version": "1",
            }
        )

    cache = SchemaCatalogCache(
        builder,
        ttl_seconds=10,
        clock=clock,
    )

    first = cache.get()
    clock.advance(10)
    second = cache.get()

    assert first is not second
    assert first == second
    assert build_count == 2
    assert cache.generation == 2


def test_zero_ttl_disables_reuse() -> None:
    clock = MutableClock()
    build_count = 0

    def builder() -> SchemaCatalog:
        nonlocal build_count
        build_count += 1

        return build_schema_catalog().model_copy()

    cache = SchemaCatalogCache(
        builder,
        ttl_seconds=0,
        clock=clock,
    )

    first = cache.get()
    second = cache.get()

    assert first is not second
    assert build_count == 2
    assert cache.generation == 2
    assert cache.is_cached is False


def test_invalidate_discards_cached_catalog() -> None:
    clock = MutableClock()
    cache = SchemaCatalogCache(
        lambda: build_schema_catalog().model_copy(),
        ttl_seconds=30,
        clock=clock,
    )

    first = cache.get()
    cache.invalidate()
    second = cache.get()

    assert first is not second
    assert cache.generation == 2
    assert cache.is_cached is True


def test_builder_failure_is_not_cached() -> None:
    attempts = 0

    def builder() -> SchemaCatalog:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise RuntimeError("catalog construction failed")

        return build_schema_catalog()

    cache = SchemaCatalogCache(
        builder,
        ttl_seconds=30,
    )

    with pytest.raises(
        RuntimeError,
        match="catalog construction failed",
    ):
        cache.get()

    assert cache.generation == 0
    assert cache.is_cached is False

    catalog = cache.get()

    assert catalog.schema_name == "retail"
    assert attempts == 2
    assert cache.generation == 1


def test_concurrent_calls_build_catalog_once() -> None:
    worker_count = 8
    start = Event()
    build_count = 0

    def builder() -> SchemaCatalog:
        nonlocal build_count
        build_count += 1

        return build_schema_catalog()

    cache = SchemaCatalogCache(
        builder,
        ttl_seconds=30,
    )

    def worker() -> SchemaCatalog:
        start.wait()

        return cache.get()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker) for _ in range(worker_count)]
        start.set()
        catalogs = [future.result(timeout=5) for future in futures]

    assert build_count == 1
    assert cache.generation == 1
    assert all(catalog is catalogs[0] for catalog in catalogs)


def test_cache_factory_uses_configured_ttl() -> None:
    settings = Settings(
        _env_file=None,
        schema_cache_ttl_seconds=42,
    )

    cache = create_schema_catalog_cache(settings)

    assert cache.ttl_seconds == 42
    assert cache.generation == 0
    assert cache.is_cached is False
