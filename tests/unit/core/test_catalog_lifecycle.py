from dataclasses import dataclass

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


class FakeDatabasePools:
    def __init__(self) -> None:
        self.events: list[str] = []

    def open(self) -> None:
        self.events.append("pools:open")

    def close(self) -> None:
        self.events.append("pools:close")


@dataclass(frozen=True, slots=True)
class FakeCatalogCache:
    name: str = "schema-catalog-cache"


def test_application_lifespan_publishes_catalog_cache() -> None:
    settings = Settings(_env_file=None)
    pools = FakeDatabasePools()
    cache = FakeCatalogCache()
    pool_factory_settings: list[Settings] = []
    cache_factory_settings: list[Settings] = []

    def pool_factory(
        configured_settings: Settings,
    ) -> FakeDatabasePools:
        pool_factory_settings.append(configured_settings)

        return pools

    def cache_factory(
        configured_settings: Settings,
    ) -> FakeCatalogCache:
        cache_factory_settings.append(configured_settings)

        return cache

    application = create_app(
        settings=settings,
        pool_factory=pool_factory,
        catalog_cache_factory=cache_factory,
    )

    assert not hasattr(
        application.state,
        "schema_catalog_cache",
    )

    with TestClient(application) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert application.state.database_ready is True
        assert application.state.schema_catalog_cache is cache
        assert pools.events == ["pools:open"]

    assert application.state.database_ready is False
    assert pools.events == [
        "pools:open",
        "pools:close",
    ]
    assert pool_factory_settings == [settings]
    assert cache_factory_settings == [settings]
