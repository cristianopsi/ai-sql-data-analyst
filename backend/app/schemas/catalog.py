from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    column_name: str = Field(min_length=1)


class CatalogColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    nullable: bool
    primary_key: bool
    references: tuple[CatalogReference, ...] = ()


class CatalogTable(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    columns: tuple[CatalogColumn, ...] = Field(min_length=1)


class SchemaCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    catalog_version: Literal["1"] = "1"
    schema_name: str = Field(min_length=1)
    tables: tuple[CatalogTable, ...] = Field(min_length=1)


class CatalogUnavailableResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    detail: Literal["Schema catalog is unavailable"] = "Schema catalog is unavailable"
