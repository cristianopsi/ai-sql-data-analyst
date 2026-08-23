from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.services.catalog_cache import (
    SchemaCatalogCache,
)
from backend.app.services.grounding_context import (
    GroundingContextService,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)


class StubDatabasePools:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def test_lifespan_publishes_grounding_service() -> None:
    settings = Settings(
        _env_file=None,
    )
    pools = StubDatabasePools()
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )
    created_services: list[GroundingContextService] = []

    def pool_factory(
        configured_settings: Settings,
    ) -> StubDatabasePools:
        assert configured_settings is settings
        return pools

    def cache_factory(
        configured_settings: Settings,
    ) -> SchemaCatalogCache:
        assert configured_settings is settings
        return cache

    def service_factory(
        configured_settings: Settings,
        configured_cache: SchemaCatalogCache,
    ) -> GroundingContextService:
        assert configured_settings is settings
        assert configured_cache is cache

        service = GroundingContextService(
            configured_cache,
            max_question_length=(configured_settings.max_question_length),
        )
        created_services.append(service)

        return service

    application = create_app(
        settings=settings,
        pool_factory=pool_factory,
        catalog_cache_factory=(cache_factory),
        grounding_context_service_factory=(service_factory),
    )

    assert cache.generation == 0

    with TestClient(application):
        service = application.state.grounding_context_service

        assert service is created_services[0]
        assert pools.opened is True
        assert cache.generation == 0

        context = service.build("Faturamento por região em 2025")

        assert context.grounding_status == "grounded"
        assert cache.generation == 1

    assert pools.closed is True
