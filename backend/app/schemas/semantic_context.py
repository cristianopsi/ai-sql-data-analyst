from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.app.schemas.catalog import (
    CatalogTable,
)
from backend.app.schemas.grounding import (
    GroundedSemanticValue,
    GroundingStatus,
)
from backend.app.schemas.semantic import (
    SemanticBusinessRule,
    SemanticDimension,
    SemanticMetric,
    SemanticRelationship,
)


class CompactGroundingContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    context_version: Literal["1"] = "1"
    semantic_version: str
    catalog_version: str
    grounding_status: GroundingStatus
    normalized_question: str | None = None
    metrics: tuple[SemanticMetric, ...] = ()
    dimensions: tuple[
        SemanticDimension,
        ...,
    ] = ()
    values: tuple[
        GroundedSemanticValue,
        ...,
    ] = ()
    tables: tuple[CatalogTable, ...] = ()
    relationships: tuple[
        SemanticRelationship,
        ...,
    ] = ()
    business_rules: tuple[
        SemanticBusinessRule,
        ...,
    ] = ()
