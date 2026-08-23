from collections.abc import Callable
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.schemas.catalog import (
    SchemaCatalog,
)
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.services.question_grounding import (
    DEFAULT_MAX_QUESTION_LENGTH,
    ground_question,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)
from backend.app.services.semantic_layer import (
    build_semantic_layer,
)

DEFAULT_MAX_CONTEXT_CHARACTERS = 20_000


class GroundingContextError(ValueError):
    """Raised when safe compact context cannot be constructed."""


class SchemaCatalogProvider(Protocol):
    def get(self) -> SchemaCatalog:
        """Return the current safe schema catalog."""


class GroundingContextService:
    """Build compact grounding context from the managed catalog cache."""

    def __init__(
        self,
        catalog_provider: SchemaCatalogProvider,
        *,
        max_question_length: int,
        max_context_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
    ) -> None:
        if max_question_length < 1:
            raise GroundingContextError("max_question_length must be positive")

        if max_context_characters < 1:
            raise GroundingContextError("max_context_characters must be positive")

        self._catalog_provider = catalog_provider
        self._max_question_length = max_question_length
        self._max_context_characters = max_context_characters

    @property
    def max_question_length(self) -> int:
        return self._max_question_length

    @property
    def max_context_characters(self) -> int:
        return self._max_context_characters

    def build(
        self,
        question: str,
    ) -> CompactGroundingContext:
        catalog = self._catalog_provider.get()

        return build_grounding_context(
            question,
            catalog,
            max_question_length=(self._max_question_length),
        )

    def serialize(
        self,
        question: str,
    ) -> str:
        context = self.build(question)

        return serialize_grounding_context(
            context,
            max_characters=(self._max_context_characters),
        )


type GroundingContextServiceFactory = Callable[
    [Settings, SchemaCatalogProvider],
    GroundingContextService,
]


def create_grounding_context_service(
    settings: Settings,
    catalog_provider: SchemaCatalogProvider,
) -> GroundingContextService:
    """Create the application grounding-context service."""
    return GroundingContextService(
        catalog_provider,
        max_question_length=(settings.max_question_length),
    )


def build_grounding_context(
    question: str,
    catalog: SchemaCatalog | None = None,
    *,
    max_question_length: int = (DEFAULT_MAX_QUESTION_LENGTH),
) -> CompactGroundingContext:
    """Build minimal catalog and semantics for a grounded question."""
    active_catalog = catalog if catalog is not None else build_schema_catalog()
    semantic_layer = build_semantic_layer(active_catalog)
    grounding = ground_question(
        question,
        semantic_layer,
        max_question_length=(max_question_length),
    )

    include_schema_context = grounding.status in {
        "grounded",
        "ambiguous",
    }

    if not include_schema_context:
        return CompactGroundingContext(
            semantic_version=(semantic_layer.semantic_version),
            catalog_version=(active_catalog.catalog_version),
            grounding_status=(grounding.status),
        )

    tables_by_name = {table.name: table for table in active_catalog.tables}
    metrics_by_name = {metric.name: metric for metric in semantic_layer.metrics}
    dimensions_by_name = {dimension.name: dimension for dimension in (semantic_layer.dimensions)}
    relationships_by_name = {
        relationship.name: relationship for relationship in (semantic_layer.relationships)
    }
    rules_by_name = {rule.name: rule for rule in (semantic_layer.business_rules)}

    try:
        selected_tables = tuple(tables_by_name[table_name] for table_name in grounding.tables)
        selected_metrics = tuple(metrics_by_name[metric_name] for metric_name in grounding.metrics)
        selected_dimensions = tuple(
            dimensions_by_name[dimension_name] for dimension_name in (grounding.dimensions)
        )
        selected_relationships = tuple(
            relationships_by_name[relationship_name]
            for relationship_name in (grounding.relationships)
        )
        selected_rules = tuple(rules_by_name[rule_name] for rule_name in (grounding.business_rules))
    except KeyError as error:
        raise GroundingContextError(
            "Grounding references an unknown safe semantic object"
        ) from error

    return CompactGroundingContext(
        semantic_version=(semantic_layer.semantic_version),
        catalog_version=(active_catalog.catalog_version),
        grounding_status=grounding.status,
        normalized_question=(grounding.normalized_question),
        metrics=selected_metrics,
        dimensions=selected_dimensions,
        values=grounding.values,
        tables=selected_tables,
        relationships=(selected_relationships),
        business_rules=selected_rules,
    )


def serialize_grounding_context(
    context: CompactGroundingContext,
    *,
    max_characters: int = (DEFAULT_MAX_CONTEXT_CHARACTERS),
) -> str:
    """Serialize compact context without truncating its contract."""
    if max_characters < 1:
        raise GroundingContextError("max_characters must be positive")

    serialized = context.model_dump_json()

    if len(serialized) > max_characters:
        raise GroundingContextError("Grounding context exceeds maximum size")

    return serialized
