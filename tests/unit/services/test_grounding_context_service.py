import pytest

from backend.app.core.config import Settings
from backend.app.services.catalog_cache import (
    SchemaCatalogCache,
)
from backend.app.services.grounding_context import (
    DEFAULT_MAX_CONTEXT_CHARACTERS,
    GroundingContextError,
    GroundingContextService,
    create_grounding_context_service,
)
from backend.app.services.question_grounding import (
    QuestionGroundingError,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)


def test_service_uses_catalog_cache_lazily() -> None:
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )
    service = GroundingContextService(
        cache,
        max_question_length=2_000,
    )

    assert cache.generation == 0

    first = service.build("Faturamento por região em 2025")
    second = service.build("Pedidos por canal")

    assert first.grounding_status == "grounded"
    assert second.grounding_status == "grounded"
    assert cache.generation == 1


def test_service_preserves_question_limit() -> None:
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )
    service = GroundingContextService(
        cache,
        max_question_length=5,
    )

    with pytest.raises(
        QuestionGroundingError,
        match="exceeds maximum length",
    ):
        service.build("pergunta longa")


def test_service_serialization_limit_fails_closed() -> None:
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )
    service = GroundingContextService(
        cache,
        max_question_length=2_000,
        max_context_characters=100,
    )

    with pytest.raises(
        GroundingContextError,
        match="exceeds maximum size",
    ):
        service.serialize("Pedidos por região")


def test_service_factory_uses_safe_settings() -> None:
    settings = Settings(
        _env_file=None,
        max_question_length=432,
    )
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )

    service = create_grounding_context_service(
        settings,
        cache,
    )

    assert service.max_question_length == 432
    assert service.max_context_characters == DEFAULT_MAX_CONTEXT_CHARACTERS


def test_restricted_service_output_is_sanitized() -> None:
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )
    service = GroundingContextService(
        cache,
        max_question_length=2_000,
    )

    serialized = service.serialize("Liste os emails dos clientes")

    assert "email" not in serialized
    assert "document_number" not in serialized
