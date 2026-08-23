from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import Connection, sql

from backend.app.db.seed_generation import GeneratedDataset, TableData

EXPECTED_ALEMBIC_REVISION = "4b9f67039f0b"
RETAIL_SCHEMA = "retail"

EXPECTED_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "regions": (
        "id",
        "code",
        "name",
        "country_code",
    ),
    "categories": (
        "id",
        "name",
        "description",
    ),
    "customers": (
        "id",
        "external_id",
        "region_id",
        "full_name",
        "email",
        "document_number",
        "segment",
        "active",
        "created_at",
    ),
    "products": (
        "id",
        "category_id",
        "sku",
        "name",
        "unit_cost",
        "list_price",
        "active",
        "created_at",
    ),
    "orders": (
        "id",
        "order_number",
        "customer_id",
        "region_id",
        "placed_at",
        "status",
        "channel",
        "discount_amount",
        "shipping_amount",
        "created_at",
    ),
    "order_items": (
        "id",
        "order_id",
        "product_id",
        "quantity",
        "unit_price",
        "unit_cost",
        "discount_amount",
    ),
    "payments": (
        "id",
        "order_id",
        "transaction_reference",
        "method",
        "status",
        "amount",
        "paid_at",
        "created_at",
    ),
    "sales_targets": (
        "id",
        "region_id",
        "target_month",
        "revenue_target",
        "orders_target",
    ),
}

TABLE_LOAD_ORDER = tuple(EXPECTED_TABLE_COLUMNS)


class DatasetLoadError(RuntimeError):
    """Base error raised by the controlled dataset loader."""


class DatasetContractError(DatasetLoadError):
    """Raised when generated data does not match the database contract."""


class DatabaseStateError(DatasetLoadError):
    """Raised when the database is not ready for an initial seed."""


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    revision: str | None
    tables: tuple[str, ...]
    row_counts: Mapping[str, int]


def validate_dataset_contract(dataset: GeneratedDataset) -> None:
    actual_order = tuple(dataset.tables)

    if actual_order != TABLE_LOAD_ORDER:
        raise DatasetContractError(
            f"Dataset table order mismatch: expected {TABLE_LOAD_ORDER}, received {actual_order}"
        )

    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        table_data = dataset.tables[table_name]

        if table_data.columns != expected_columns:
            raise DatasetContractError(
                f"Dataset columns mismatch for {table_name}: "
                f"expected {expected_columns}, received {table_data.columns}"
            )

        _validate_row_widths(table_name, table_data)


def _validate_row_widths(
    table_name: str,
    table_data: TableData,
) -> None:
    expected_width = len(table_data.columns)

    for row_number, row in enumerate(table_data.rows, start=1):
        if len(row) != expected_width:
            raise DatasetContractError(
                f"Dataset row width mismatch for {table_name} "
                f"at row {row_number}: expected {expected_width}, "
                f"received {len(row)}"
            )


def inspect_database(
    connection: Connection[Any],
) -> DatabaseSnapshot:
    revision = _current_revision(connection)
    tables = _retail_tables(connection)

    expected_table_set = set(TABLE_LOAD_ORDER)

    row_counts = database_row_counts(connection) if set(tables) == expected_table_set else {}

    return DatabaseSnapshot(
        revision=revision,
        tables=tables,
        row_counts=row_counts,
    )


def _current_revision(
    connection: Connection[Any],
) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version_num
            FROM public.alembic_version
            """
        )
        row = cursor.fetchone()

    return None if row is None else str(row[0])


def _retail_tables(
    connection: Connection[Any],
) -> tuple[str, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (RETAIL_SCHEMA,),
        )
        rows = cursor.fetchall()

    return tuple(str(row[0]) for row in rows)


def database_row_counts(
    connection: Connection[Any],
) -> dict[str, int]:
    counts: dict[str, int] = {}

    with connection.cursor() as cursor:
        for table_name in TABLE_LOAD_ORDER:
            statement = sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier(
                    RETAIL_SCHEMA,
                    table_name,
                )
            )
            cursor.execute(statement)
            row = cursor.fetchone()

            if row is None:
                raise DatabaseStateError(f"Could not count rows in {RETAIL_SCHEMA}.{table_name}")

            counts[table_name] = int(row[0])

    return counts


def validate_database_snapshot(
    snapshot: DatabaseSnapshot,
) -> None:
    if snapshot.revision != EXPECTED_ALEMBIC_REVISION:
        raise DatabaseStateError(
            "Database revision mismatch: "
            f"expected {EXPECTED_ALEMBIC_REVISION}, "
            f"received {snapshot.revision}"
        )

    expected_tables = set(TABLE_LOAD_ORDER)
    actual_tables = set(snapshot.tables)

    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        unexpected = sorted(actual_tables - expected_tables)

        raise DatabaseStateError(
            f"Database table set mismatch: missing={missing}, unexpected={unexpected}"
        )

    if set(snapshot.row_counts) != expected_tables:
        raise DatabaseStateError("Database row-count snapshot is incomplete")

    nonempty_tables = {
        table_name: row_count
        for table_name, row_count in snapshot.row_counts.items()
        if row_count != 0
    }

    if nonempty_tables:
        raise DatabaseStateError(
            f"Retail database must be empty before initial seed: {nonempty_tables}"
        )


def load_dataset(
    connection: Connection[Any],
    dataset: GeneratedDataset,
) -> dict[str, int]:
    validate_dataset_contract(dataset)
    expected_counts = dataset.row_counts
    loaded_counts: dict[str, int] = {}

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '120s'")
            cursor.execute("SET LOCAL lock_timeout = '5s'")

        initial_snapshot = inspect_database(connection)
        validate_database_snapshot(initial_snapshot)

        _lock_retail_tables(connection)

        locked_snapshot = inspect_database(connection)
        validate_database_snapshot(locked_snapshot)

        for table_name in TABLE_LOAD_ORDER:
            _copy_table(
                connection,
                table_name,
                dataset.tables[table_name],
            )

        _synchronize_identity_sequences(connection)
        _analyze_tables(connection)

        loaded_counts = database_row_counts(connection)

        if loaded_counts != expected_counts:
            raise DatasetLoadError(
                "Post-load row counts do not match generated data: "
                f"expected {expected_counts}, received {loaded_counts}"
            )

    return loaded_counts


def _lock_retail_tables(
    connection: Connection[Any],
) -> None:
    table_identifiers = [
        sql.Identifier(RETAIL_SCHEMA, table_name) for table_name in TABLE_LOAD_ORDER
    ]

    statement = sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
        sql.SQL(", ").join(table_identifiers)
    )

    with connection.cursor() as cursor:
        cursor.execute(statement)


def _copy_table(
    connection: Connection[Any],
    table_name: str,
    table_data: TableData,
) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(RETAIL_SCHEMA, table_name),
        sql.SQL(", ").join(sql.Identifier(column) for column in table_data.columns),
    )

    with connection.cursor() as cursor, cursor.copy(statement) as copy:
        for row in table_data.rows:
            copy.write_row(row)


def _synchronize_identity_sequences(
    connection: Connection[Any],
) -> None:
    with connection.cursor() as cursor:
        for table_name in TABLE_LOAD_ORDER:
            qualified_table = f"{RETAIL_SCHEMA}.{table_name}"

            statement = sql.SQL(
                """
                SELECT setval(
                    pg_get_serial_sequence({}, 'id'),
                    COALESCE(MAX(id), 1),
                    MAX(id) IS NOT NULL
                )
                FROM {}
                """
            ).format(
                sql.Literal(qualified_table),
                sql.Identifier(
                    RETAIL_SCHEMA,
                    table_name,
                ),
            )

            cursor.execute(statement)
            cursor.fetchone()


def _analyze_tables(
    connection: Connection[Any],
) -> None:
    with connection.cursor() as cursor:
        for table_name in TABLE_LOAD_ORDER:
            statement = sql.SQL("ANALYZE {}").format(
                sql.Identifier(
                    RETAIL_SCHEMA,
                    table_name,
                )
            )
            cursor.execute(statement)
