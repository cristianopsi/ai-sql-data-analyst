from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type SemanticScalar = str | bool
type DimensionKind = Literal[
    "categorical",
    "temporal",
    "identifier",
]
type TimeGranularity = Literal[
    "day",
    "month",
    "quarter",
    "year",
]
type MetricAggregation = Literal[
    "average",
    "count_distinct",
    "sum",
]
type MetricUnit = Literal[
    "brl",
    "count",
    "units",
]
type FilterOperator = Literal["equals"]
type RelationshipCardinality = Literal["many_to_one"]


class SemanticColumnReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    column_name: str = Field(min_length=1)


class SemanticValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SemanticScalar
    label: str = Field(min_length=1)
    synonyms: tuple[str, ...] = ()


class SemanticDimension(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: SemanticColumnReference
    kind: DimensionKind
    synonyms: tuple[str, ...] = ()
    values: tuple[SemanticValue, ...] = ()
    time_granularities: tuple[
        TimeGranularity,
        ...,
    ] = ()


class SemanticFilter(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: SemanticColumnReference
    operator: FilterOperator
    value: SemanticScalar


class SemanticMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aggregation: MetricAggregation
    source: SemanticColumnReference
    unit: MetricUnit
    synonyms: tuple[str, ...] = ()
    filters: tuple[SemanticFilter, ...] = ()


class SemanticRelationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    from_column: SemanticColumnReference
    to_column: SemanticColumnReference
    cardinality: RelationshipCardinality


class SemanticBusinessRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    related_metrics: tuple[str, ...] = ()


class SemanticLayer(BaseModel):
    model_config = ConfigDict(frozen=True)

    semantic_version: Literal["1"] = "1"
    catalog_version: str = Field(min_length=1)
    schema_name: str = Field(min_length=1)
    dimensions: tuple[
        SemanticDimension,
        ...,
    ] = Field(min_length=1)
    metrics: tuple[
        SemanticMetric,
        ...,
    ] = Field(min_length=1)
    relationships: tuple[
        SemanticRelationship,
        ...,
    ] = Field(min_length=1)
    business_rules: tuple[
        SemanticBusinessRule,
        ...,
    ] = Field(min_length=1)
