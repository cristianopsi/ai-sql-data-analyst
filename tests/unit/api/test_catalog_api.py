from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.catalog import router
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas.catalog import (
    SchemaCatalog,
)
from backend.app.services.catalog_cache import (
    SchemaCatalogCache,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)


class FakeDatabasePools:
    def open(self) -> None:
        return None

    def close(self) -> None:
        return None


def create_catalog_application(
    cache: SchemaCatalogCache,
) -> FastAPI:
    settings = Settings(
        _env_file=None,
        schema_cache_ttl_seconds=60,
    )

    return create_app(
        settings=settings,
        pool_factory=(lambda configured_settings: FakeDatabasePools()),
        catalog_cache_factory=(lambda configured_settings: cache),
    )


def test_catalog_endpoint_returns_safe_catalog() -> None:
    cache = SchemaCatalogCache(
        build_schema_catalog,
        ttl_seconds=60,
    )
    application = create_catalog_application(cache)

    with TestClient(application) as client:
        response = client.get("/api/v1/schema/catalog")
        openapi = client.get("/openapi.json").json()

    body = response.json()

    assert response.status_code == 200
    assert body["catalog_version"] == "1"
    assert body["schema_name"] == "retail"
    assert len(body["tables"]) == 8
    assert sum(len(table["columns"]) for table in body["tables"]) == 52
    assert response.headers["cache-control"] == "private, max-age=60"
    assert response.headers["etag"].startswith('"')
    assert response.headers["x-catalog-generation"] == "1"
    assert response.headers["x-catalog-version"] == "1"
    assert "/api/v1/schema/catalog" in openapi["paths"]
    assert "email" not in response.text
    assert "document_number" not in (response.text)


def test_catalog_endpoint_supports_conditional_get() -> None:
    cache = SchemaCatalogCache(
        build_schema_catalog,
        ttl_seconds=60,
    )
    application = create_catalog_application(cache)

    with TestClient(application) as client:
        first = client.get("/api/v1/schema/catalog")
        second = client.get(
            "/api/v1/schema/catalog",
            headers={"If-None-Match": (first.headers["etag"])},
        )

    assert first.status_code == 200
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == (first.headers["etag"])
    assert second.headers["x-catalog-generation"] == "1"
    assert cache.generation == 1


def test_catalog_endpoint_sanitizes_builder_failure() -> None:
    sensitive_failure = "database-password-marker"

    def failing_builder() -> SchemaCatalog:
        raise RuntimeError(sensitive_failure)

    cache = SchemaCatalogCache(
        failing_builder,
        ttl_seconds=60,
    )
    application = create_catalog_application(cache)

    with TestClient(application) as client:
        response = client.get("/api/v1/schema/catalog")

    assert response.status_code == 503
    assert response.json() == {"detail": ("Schema catalog is unavailable")}
    assert sensitive_failure not in response.text
    assert "password" not in response.text
    assert cache.generation == 0


def test_catalog_endpoint_requires_initialized_cache() -> None:
    application = FastAPI()
    application.include_router(router)

    with TestClient(application) as client:
        response = client.get("/api/v1/schema/catalog")

    assert response.status_code == 503
    assert response.json() == {"detail": ("Schema catalog is unavailable")}
