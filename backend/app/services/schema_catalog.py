from sqlalchemy import Column, MetaData, Table

import backend.app.db.models  # noqa: F401
from backend.app.db.base import metadata
from backend.app.db.grant_provisioning import (
    RetailGrantPolicy,
)
from backend.app.schemas.catalog import (
    CatalogColumn,
    CatalogReference,
    CatalogTable,
    SchemaCatalog,
)


class CatalogConstructionError(ValueError):
    """Raised when declared metadata violates the catalog contract."""


def _required_comment(
    comment: str | None,
    object_name: str,
) -> str:
    if comment is None or not comment.strip():
        raise CatalogConstructionError(f"Catalog object lacks documentation: {object_name}")

    return comment.strip()


def _column_references(
    column: Column[object],
) -> tuple[CatalogReference, ...]:
    references: list[CatalogReference] = []

    for foreign_key in sorted(
        column.foreign_keys,
        key=lambda current_key: current_key.target_fullname,
    ):
        target_column = foreign_key.column
        target_table = target_column.table

        references.append(
            CatalogReference(
                schema_name=(target_table.schema or "public"),
                table_name=target_table.name,
                column_name=target_column.name,
            )
        )

    return tuple(references)


def _catalog_column(
    table: Table,
    column_name: str,
) -> CatalogColumn:
    column = table.c[column_name]
    nullable = column.nullable

    if nullable is None:
        raise CatalogConstructionError(
            f"Catalog column lacks nullability: {table.schema}.{table.name}.{column.name}"
        )

    return CatalogColumn(
        name=column.name,
        data_type=str(column.type),
        description=_required_comment(
            column.comment,
            (f"{table.schema}.{table.name}.{column.name}"),
        ),
        nullable=nullable,
        primary_key=column.primary_key,
        references=_column_references(column),
    )


def _catalog_table(
    table: Table,
    allowed_columns: tuple[str, ...],
) -> CatalogTable:
    return CatalogTable(
        schema_name=table.schema or "public",
        name=table.name,
        description=_required_comment(
            table.comment,
            f"{table.schema}.{table.name}",
        ),
        columns=tuple(
            _catalog_column(
                table,
                column_name,
            )
            for column_name in allowed_columns
        ),
    )


def build_schema_catalog(
    source_metadata: MetaData = metadata,
    policy: RetailGrantPolicy | None = None,
) -> SchemaCatalog:
    """Build a deterministic catalog filtered by database policy."""
    active_policy = policy or RetailGrantPolicy()

    expected_tables = set(active_policy.full_select_tables) | {
        active_policy.column_restricted_table
    }
    schema_tables = {
        table.name: table
        for table in source_metadata.tables.values()
        if table.schema == active_policy.schema_name
    }
    actual_tables = set(schema_tables)

    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        unexpected = sorted(actual_tables - expected_tables)

        raise CatalogConstructionError(
            f"Catalog table set mismatch: missing={missing}, unexpected={unexpected}"
        )

    restricted_table = schema_tables[active_policy.column_restricted_table]
    declared_restricted_table_columns = set(restricted_table.columns.keys())
    policy_restricted_table_columns = set(active_policy.allowed_customer_columns) | set(
        active_policy.restricted_customer_columns
    )

    if declared_restricted_table_columns != policy_restricted_table_columns:
        raise CatalogConstructionError("Column-restricted table does not match the grant policy")

    catalog_tables: list[CatalogTable] = []

    for table_name in sorted(expected_tables):
        table = schema_tables[table_name]

        if table_name == active_policy.column_restricted_table:
            allowed_columns = active_policy.allowed_customer_columns
        else:
            allowed_columns = tuple(column.name for column in table.columns)

        catalog_tables.append(
            _catalog_table(
                table,
                allowed_columns,
            )
        )

    return SchemaCatalog(
        schema_name=active_policy.schema_name,
        tables=tuple(catalog_tables),
    )
