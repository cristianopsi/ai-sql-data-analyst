from copy import deepcopy

import pytest

from backend.app.db.seed_generation import (
    GeneratedDataset,
    SeedConfig,
    generate_dataset,
)
from backend.app.db.seed_loading import (
    EXPECTED_ALEMBIC_REVISION,
    EXPECTED_TABLE_COLUMNS,
    TABLE_LOAD_ORDER,
    DatabaseSnapshot,
    DatabaseStateError,
    DatasetContractError,
    validate_database_snapshot,
    validate_dataset_contract,
)


@pytest.fixture(scope="module")
def generated_dataset() -> GeneratedDataset:
    return generate_dataset(
        SeedConfig(
            customer_count=60,
            product_count=36,
            order_count=200,
        )
    )


def test_generated_dataset_matches_loading_contract(
    generated_dataset: GeneratedDataset,
) -> None:
    validate_dataset_contract(generated_dataset)

    assert tuple(generated_dataset.tables) == TABLE_LOAD_ORDER

    assert {
        table_name: table.columns for table_name, table in generated_dataset.tables.items()
    } == EXPECTED_TABLE_COLUMNS


def test_dataset_table_order_mismatch_is_rejected(
    generated_dataset: GeneratedDataset,
) -> None:
    changed_dataset = deepcopy(generated_dataset)
    changed_dataset.tables = dict(reversed(tuple(changed_dataset.tables.items())))

    with pytest.raises(
        DatasetContractError,
        match="table order mismatch",
    ):
        validate_dataset_contract(changed_dataset)


def test_dataset_column_mismatch_is_rejected(
    generated_dataset: GeneratedDataset,
) -> None:
    changed_dataset = deepcopy(generated_dataset)
    changed_dataset.tables["regions"].columns = (
        "id",
        "code",
        "unexpected_column",
        "country_code",
    )

    with pytest.raises(
        DatasetContractError,
        match="columns mismatch for regions",
    ):
        validate_dataset_contract(changed_dataset)


def test_dataset_row_width_mismatch_is_rejected(
    generated_dataset: GeneratedDataset,
) -> None:
    changed_dataset = deepcopy(generated_dataset)
    original_row = changed_dataset.tables["regions"].rows[0]
    changed_dataset.tables["regions"].rows[0] = original_row[:-1]

    with pytest.raises(
        DatasetContractError,
        match="row width mismatch for regions",
    ):
        validate_dataset_contract(changed_dataset)


def test_empty_database_snapshot_is_accepted() -> None:
    snapshot = DatabaseSnapshot(
        revision=EXPECTED_ALEMBIC_REVISION,
        tables=tuple(sorted(TABLE_LOAD_ORDER)),
        row_counts={table_name: 0 for table_name in TABLE_LOAD_ORDER},
    )

    validate_database_snapshot(snapshot)


def test_wrong_database_revision_is_rejected() -> None:
    snapshot = DatabaseSnapshot(
        revision="unexpected_revision",
        tables=tuple(sorted(TABLE_LOAD_ORDER)),
        row_counts={table_name: 0 for table_name in TABLE_LOAD_ORDER},
    )

    with pytest.raises(
        DatabaseStateError,
        match="revision mismatch",
    ):
        validate_database_snapshot(snapshot)


def test_missing_database_table_is_rejected() -> None:
    snapshot = DatabaseSnapshot(
        revision=EXPECTED_ALEMBIC_REVISION,
        tables=("regions",),
        row_counts={},
    )

    with pytest.raises(
        DatabaseStateError,
        match="table set mismatch",
    ):
        validate_database_snapshot(snapshot)


def test_nonempty_database_is_rejected() -> None:
    row_counts = {table_name: 0 for table_name in TABLE_LOAD_ORDER}
    row_counts["orders"] = 1

    snapshot = DatabaseSnapshot(
        revision=EXPECTED_ALEMBIC_REVISION,
        tables=tuple(sorted(TABLE_LOAD_ORDER)),
        row_counts=row_counts,
    )

    with pytest.raises(
        DatabaseStateError,
        match="must be empty",
    ):
        validate_database_snapshot(snapshot)
