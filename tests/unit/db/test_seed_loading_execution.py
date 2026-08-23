from datetime import date
from typing import Literal

import pytest

from backend.app.db import seed_loading
from backend.app.db.seed_generation import (
    GeneratedDataset,
    TableData,
)
from backend.app.db.seed_loading import (
    EXPECTED_ALEMBIC_REVISION,
    EXPECTED_TABLE_COLUMNS,
    TABLE_LOAD_ORDER,
    DatabaseSnapshot,
    DatabaseStateError,
    DatasetLoadError,
)


class FakeContext[ContextValue]:
    def __init__(self, value: ContextValue) -> None:
        self.value = value

    def __enter__(self) -> ContextValue:
        return self.value

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> Literal[False]:
        return False


class FakeCopy:
    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []

    def write_row(
        self,
        row: tuple[object, ...],
    ) -> None:
        self.rows.append(row)


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_values: list[tuple[object, ...] | None] | None = None,
        fetchall_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_rows = list(fetchall_rows or [])
        self.executed: list[tuple[object, object | None]] = []
        self.copy_statements: list[object] = []
        self.copy_writer = FakeCopy()

    def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> None:
        self.executed.append((statement, parameters))

    def fetchone(
        self,
    ) -> tuple[object, ...] | None:
        if self.fetchone_values:
            return self.fetchone_values.pop(0)

        return (1,)

    def fetchall(
        self,
    ) -> list[tuple[object, ...]]:
        return list(self.fetchall_rows)

    def copy(
        self,
        statement: object,
    ) -> FakeContext[FakeCopy]:
        self.copy_statements.append(statement)
        return FakeContext(self.copy_writer)


class FakeConnection:
    def __init__(
        self,
        cursor: FakeCursor | None = None,
    ) -> None:
        self.fake_cursor = cursor or FakeCursor()
        self.transaction_count = 0

    def cursor(
        self,
    ) -> FakeContext[FakeCursor]:
        return FakeContext(self.fake_cursor)

    def transaction(
        self,
    ) -> FakeContext[None]:
        self.transaction_count += 1
        return FakeContext(None)


def empty_dataset() -> GeneratedDataset:
    return GeneratedDataset(
        tables={
            table_name: TableData(columns=columns)
            for table_name, columns in EXPECTED_TABLE_COLUMNS.items()
        },
        seed=20260823,
        start_date=date(2022, 1, 1),
        reference_date=date(2026, 8, 1),
    )


def empty_snapshot() -> DatabaseSnapshot:
    return DatabaseSnapshot(
        revision=EXPECTED_ALEMBIC_REVISION,
        tables=tuple(sorted(TABLE_LOAD_ORDER)),
        row_counts={table_name: 0 for table_name in TABLE_LOAD_ORDER},
    )


def test_inspect_database_reads_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    expected_counts = {table_name: 0 for table_name in TABLE_LOAD_ORDER}

    monkeypatch.setattr(
        seed_loading,
        "_current_revision",
        lambda _: EXPECTED_ALEMBIC_REVISION,
    )
    monkeypatch.setattr(
        seed_loading,
        "_retail_tables",
        lambda _: tuple(sorted(TABLE_LOAD_ORDER)),
    )
    monkeypatch.setattr(
        seed_loading,
        "database_row_counts",
        lambda _: expected_counts,
    )

    snapshot = seed_loading.inspect_database(connection)

    assert snapshot.revision == (EXPECTED_ALEMBIC_REVISION)
    assert snapshot.tables == tuple(sorted(TABLE_LOAD_ORDER))
    assert snapshot.row_counts == expected_counts


def test_inspect_database_skips_counts_for_wrong_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()

    def fail_if_called(
        connection_value: object,
    ) -> None:
        raise AssertionError("Row counts should not be requested")

    monkeypatch.setattr(
        seed_loading,
        "_current_revision",
        lambda _: EXPECTED_ALEMBIC_REVISION,
    )
    monkeypatch.setattr(
        seed_loading,
        "_retail_tables",
        lambda _: ("unexpected_table",),
    )
    monkeypatch.setattr(
        seed_loading,
        "database_row_counts",
        fail_if_called,
    )

    snapshot = seed_loading.inspect_database(connection)

    assert snapshot.tables == ("unexpected_table",)
    assert snapshot.row_counts == {}


def test_current_revision_handles_row_and_no_row() -> None:
    revision_connection = FakeConnection(
        FakeCursor(
            fetchone_values=[
                (EXPECTED_ALEMBIC_REVISION,),
            ]
        )
    )
    empty_connection = FakeConnection(FakeCursor(fetchone_values=[None]))

    assert seed_loading._current_revision(revision_connection) == EXPECTED_ALEMBIC_REVISION

    assert seed_loading._current_revision(empty_connection) is None


def test_retail_tables_are_returned_as_tuple() -> None:
    connection = FakeConnection(
        FakeCursor(
            fetchall_rows=[
                ("categories",),
                ("regions",),
            ]
        )
    )

    tables = seed_loading._retail_tables(connection)

    assert tables == (
        "categories",
        "regions",
    )
    assert connection.fake_cursor.executed[0][1] == ("retail",)


def test_database_row_counts_reads_all_tables() -> None:
    expected_counts = {
        table_name: index for index, table_name in enumerate(TABLE_LOAD_ORDER, start=1)
    }
    connection = FakeConnection(
        FakeCursor(fetchone_values=[(row_count,) for row_count in expected_counts.values()])
    )

    counts = seed_loading.database_row_counts(connection)

    assert counts == expected_counts
    assert len(connection.fake_cursor.executed) == len(TABLE_LOAD_ORDER)


def test_database_row_counts_rejects_missing_result() -> None:
    connection = FakeConnection(FakeCursor(fetchone_values=[None]))

    with pytest.raises(
        DatabaseStateError,
        match="Could not count rows",
    ):
        seed_loading.database_row_counts(connection)


def test_incomplete_row_count_snapshot_is_rejected() -> None:
    snapshot = DatabaseSnapshot(
        revision=EXPECTED_ALEMBIC_REVISION,
        tables=tuple(sorted(TABLE_LOAD_ORDER)),
        row_counts={"regions": 0},
    )

    with pytest.raises(
        DatabaseStateError,
        match="snapshot is incomplete",
    ):
        seed_loading.validate_database_snapshot(snapshot)


def test_load_dataset_orchestrates_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = empty_dataset()
    connection = FakeConnection()
    snapshot = empty_snapshot()
    copied_tables: list[str] = []
    lifecycle_events: list[str] = []

    monkeypatch.setattr(
        seed_loading,
        "inspect_database",
        lambda _: snapshot,
    )
    monkeypatch.setattr(
        seed_loading,
        "_lock_retail_tables",
        lambda _: lifecycle_events.append("lock"),
    )
    monkeypatch.setattr(
        seed_loading,
        "_copy_table",
        lambda _, table_name, __: copied_tables.append(table_name),
    )
    monkeypatch.setattr(
        seed_loading,
        "_synchronize_identity_sequences",
        lambda _: lifecycle_events.append("sequences"),
    )
    monkeypatch.setattr(
        seed_loading,
        "_analyze_tables",
        lambda _: lifecycle_events.append("analyze"),
    )
    monkeypatch.setattr(
        seed_loading,
        "database_row_counts",
        lambda _: dataset.row_counts,
    )

    result = seed_loading.load_dataset(
        connection,
        dataset,
    )

    assert result == dataset.row_counts
    assert connection.transaction_count == 1
    assert copied_tables == list(TABLE_LOAD_ORDER)
    assert lifecycle_events == [
        "lock",
        "sequences",
        "analyze",
    ]
    assert len(connection.fake_cursor.executed) == 2


def test_load_dataset_rejects_post_load_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = empty_dataset()
    connection = FakeConnection()
    snapshot = empty_snapshot()
    mismatched_counts = dict(dataset.row_counts)
    mismatched_counts["orders"] = 1

    monkeypatch.setattr(
        seed_loading,
        "inspect_database",
        lambda _: snapshot,
    )
    monkeypatch.setattr(
        seed_loading,
        "_lock_retail_tables",
        lambda _: None,
    )
    monkeypatch.setattr(
        seed_loading,
        "_copy_table",
        lambda *_: None,
    )
    monkeypatch.setattr(
        seed_loading,
        "_synchronize_identity_sequences",
        lambda _: None,
    )
    monkeypatch.setattr(
        seed_loading,
        "_analyze_tables",
        lambda _: None,
    )
    monkeypatch.setattr(
        seed_loading,
        "database_row_counts",
        lambda _: mismatched_counts,
    )

    with pytest.raises(
        DatasetLoadError,
        match="Post-load row counts",
    ):
        seed_loading.load_dataset(
            connection,
            dataset,
        )


def test_postgresql_statement_helpers() -> None:
    lock_connection = FakeConnection()
    seed_loading._lock_retail_tables(lock_connection)

    assert len(lock_connection.fake_cursor.executed) == 1

    copy_connection = FakeConnection()
    table_data = TableData(
        columns=("id", "code"),
        rows=[
            (1, "ONE"),
            (2, "TWO"),
        ],
    )
    seed_loading._copy_table(
        copy_connection,
        "regions",
        table_data,
    )

    assert len(copy_connection.fake_cursor.copy_statements) == 1
    assert copy_connection.fake_cursor.copy_writer.rows == [
        (1, "ONE"),
        (2, "TWO"),
    ]

    sequence_connection = FakeConnection()
    seed_loading._synchronize_identity_sequences(sequence_connection)

    assert len(sequence_connection.fake_cursor.executed) == len(TABLE_LOAD_ORDER)

    analyze_connection = FakeConnection()
    seed_loading._analyze_tables(analyze_connection)

    assert len(analyze_connection.fake_cursor.executed) == len(TABLE_LOAD_ORDER)
