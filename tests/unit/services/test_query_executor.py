from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.db.pools import (
    DatabasePools,
    RuntimeConnectionPool,
)
from backend.app.schemas.llm import (
    LLMTokenUsage,
)
from backend.app.schemas.query_execution import (
    QueryExecutionApiErrorResponse,
    QueryExecutionRequest,
    QueryExecutionResult,
    QueryResultColumnMetadata,
    QueryResultRow,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
    QueryExecutor,
    create_query_executor,
)


def build_generation_result(
    *,
    row_limit: int = 2,
) -> SQLGenerationResult:
    validated_sql = ValidatedSQL(
        proposal_version="1",
        context_version="1",
        semantic_version="1",
        catalog_version="1",
        provider="mock",
        model="deterministic-test",
        sql=(f"SELECT r.name AS region FROM retail.regions AS r LIMIT {row_limit}"),
        explanation="Return controlled regional data.",
        row_limit=row_limit,
        referenced_tables=("regions",),
        referenced_columns=("regions.name",),
        usage=LLMTokenUsage(
            input_tokens=12,
            output_tokens=8,
        ),
    )

    return SQLGenerationResult(
        validated_sql=validated_sql,
        generation_attempts=1,
        repair_attempts=0,
    )


def build_execution_result(
    *,
    columns: tuple[str, ...] = (
        "region",
        "approved_revenue",
    ),
    rows: tuple[QueryResultRow, ...] = (
        (
            "North",
            1250.5,
        ),
        (
            "South",
            975.0,
        ),
    ),
    row_count: int | None = None,
    row_limit: int = 2,
) -> QueryExecutionResult:
    resolved_row_count = len(rows) if row_count is None else row_count

    return QueryExecutionResult(
        generation=build_generation_result(
            row_limit=row_limit,
        ),
        columns=columns,
        rows=rows,
        row_count=resolved_row_count,
        statement_timeout_ms=8000,
        query_timeout_seconds=10.0,
        execution_time_ms=2.5,
    )


def test_query_execution_request_is_strict() -> None:
    request = QueryExecutionRequest(
        question="  Revenue by region  ",
    )

    assert request.question == "Revenue by region"

    error = QueryExecutionApiErrorResponse(
        detail="Query could not be executed safely",
    )

    assert error.detail == ("Query could not be executed safely")

    invalid_payloads = (
        {
            "question": "   ",
        },
        {
            "question": "x" * 10_001,
        },
        {
            "question": "Revenue",
            "unexpected": True,
        },
    )

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            QueryExecutionRequest.model_validate(payload)


def test_execution_result_accepts_json_safe_rows() -> None:
    result = QueryExecutionResult(
        generation=build_generation_result(
            row_limit=1,
        ),
        columns=(
            "name",
            "count",
            "ratio",
            "active",
            "optional",
        ),
        rows=(
            (
                "North",
                5,
                2.5,
                True,
                None,
            ),
        ),
        row_count=1,
        statement_timeout_ms=8000,
        query_timeout_seconds=10.0,
        execution_time_ms=1.25,
    )

    serialized = result.model_dump(
        mode="json",
    )

    assert result.execution_version == "1"
    assert result.execution_status == "executed"
    assert result.transaction_read_only is True
    assert result.row_count == 1
    assert serialized["rows"] == [
        [
            "North",
            5,
            2.5,
            True,
            None,
        ]
    ]


def test_execution_result_requires_matching_row_count() -> None:
    with pytest.raises(
        ValidationError,
        match="row_count must equal",
    ):
        build_execution_result(
            row_count=1,
        )


def test_execution_result_rejects_rows_with_wrong_width() -> None:
    with pytest.raises(
        ValidationError,
        match="must match the column count",
    ):
        build_execution_result(
            rows=(("North",),),
        )


def test_execution_result_rejects_duplicate_columns() -> None:
    with pytest.raises(
        ValidationError,
        match="columns must be unique",
    ):
        build_execution_result(
            columns=(
                "Region",
                "region",
            ),
        )


def test_execution_result_enforces_validated_row_limit() -> None:
    with pytest.raises(
        ValidationError,
        match="exceeds the validated row limit",
    ):
        build_execution_result(
            row_limit=1,
        )


@pytest.mark.parametrize(
    "unsafe_value",
    (
        Decimal("12.34"),
        float("nan"),
        float("inf"),
        float("-inf"),
    ),
)
def test_execution_result_rejects_non_json_values(
    unsafe_value: object,
) -> None:
    payload = {
        "generation": build_generation_result(
            row_limit=1,
        ),
        "columns": ("amount",),
        "rows": ((unsafe_value,),),
        "row_count": 1,
        "statement_timeout_ms": 8000,
        "query_timeout_seconds": 10.0,
        "execution_time_ms": 1.0,
    }

    with pytest.raises(
        ValidationError,
        match="JSON-safe|finite",
    ):
        QueryExecutionResult.model_validate(payload)


def test_execution_contract_is_immutable() -> None:
    result = build_execution_result()

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        result.row_count = 0

    assert isinstance(
        result.rows,
        tuple,
    )
    assert all(
        isinstance(
            row,
            tuple,
        )
        for row in result.rows
    )


@dataclass(frozen=True)
class FakeResultColumn:
    name: str
    type_code: int


class FakeQueryCursor:
    def __init__(
        self,
        connection: "FakeAnalyticsConnection",
    ) -> None:
        self._connection = connection
        self._rows: tuple[
            tuple[object, ...],
            ...,
        ] = ()
        self.description: tuple[FakeResultColumn, ...] | None = None

    def execute(
        self,
        statement: str,
        parameters: object | None = None,
    ) -> None:
        self._connection.statements.append(
            (
                statement,
                parameters,
            )
        )

        if "set_config(" in statement:
            if self._connection.fail_timeout_setup:
                raise RuntimeError("sensitive timeout configuration failure")

            self.description = (
                FakeResultColumn(name="statement_timeout", type_code=25),
                FakeResultColumn(name="lock_timeout", type_code=25),
                FakeResultColumn(
                    name="idle_in_transaction_session_timeout",
                    type_code=25,
                ),
            )
            self._rows = (self._connection.configured_timeouts,)
            return

        if "current_setting" in statement:
            self.description = (
                FakeResultColumn(name="transaction_read_only", type_code=25),
                FakeResultColumn(name="statement_timeout", type_code=25),
                FakeResultColumn(name="lock_timeout", type_code=25),
                FakeResultColumn(
                    name="idle_in_transaction_session_timeout",
                    type_code=25,
                ),
            )
            self._rows = (
                (
                    self._connection.read_only,
                    *self._connection.active_timeouts,
                ),
            )
            return

        if statement == self._connection.generated_sql:
            if self._connection.fail_query:
                raise RuntimeError("sensitive database failure")

            self.description = tuple(
                FakeResultColumn(
                    name=column,
                    type_code=type_code,
                )
                for column, type_code in zip(
                    self._connection.columns,
                    self._connection.type_codes,
                    strict=True,
                )
            )
            self._rows = self._connection.rows
            return

        self.description = None
        self._rows = ()

    def fetchone(
        self,
    ) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchmany(
        self,
        size: int = 0,
    ) -> list[tuple[object, ...]]:
        self._connection.fetchmany_sizes.append(size)

        return list(self._rows[:size])

    def fetchall(
        self,
    ) -> list[tuple[object, ...]]:
        self._connection.fetchall_calls += 1
        raise AssertionError("QueryExecutor must use bounded fetchmany")


class FakeAnalyticsConnection:
    def __init__(
        self,
        *,
        generated_sql: str,
        columns: tuple[str, ...],
        rows: tuple[
            tuple[object, ...],
            ...,
        ],
        type_codes: tuple[int, ...] | None = None,
        read_only: object = "on",
        configured_timeouts: tuple[object, ...] = (
            "8s",
            "2s",
            "30s",
        ),
        active_timeouts: tuple[object, ...] | None = None,
        fail_timeout_setup: bool = False,
        fail_query: bool = False,
    ) -> None:
        self.generated_sql = generated_sql
        self.columns = columns
        self.rows = rows
        self.type_codes = tuple(1043 for _ in columns) if type_codes is None else type_codes

        if len(self.type_codes) != len(self.columns):
            raise ValueError("Fake type codes must match fake columns")

        self.read_only = read_only
        self.configured_timeouts = configured_timeouts
        self.active_timeouts = configured_timeouts if active_timeouts is None else active_timeouts
        self.fail_timeout_setup = fail_timeout_setup
        self.fail_query = fail_query
        self.fetchmany_sizes: list[int] = []
        self.fetchall_calls = 0
        self.statements: list[
            tuple[
                str,
                object | None,
            ]
        ] = []
        self.events: list[str] = []

    @contextmanager
    def transaction(
        self,
    ) -> Iterator[None]:
        self.events.append("transaction:begin")

        try:
            yield
        except Exception:
            self.events.append("transaction:rollback")
            raise
        else:
            self.events.append("transaction:commit")

    @contextmanager
    def cursor(
        self,
    ) -> Iterator[FakeQueryCursor]:
        yield FakeQueryCursor(self)


class FakeAnalyticsPool:
    def __init__(
        self,
        connection: FakeAnalyticsConnection,
        *,
        fail_connection: bool = False,
    ) -> None:
        self.connection_value = connection
        self.fail_connection = fail_connection
        self.timeouts: list[float | None] = []

    @contextmanager
    def connection(
        self,
        *,
        timeout: float | None = None,
    ) -> Iterator[FakeAnalyticsConnection]:
        self.timeouts.append(timeout)

        if self.fail_connection:
            raise RuntimeError("sensitive connection failure")

        yield self.connection_value


def build_executor(
    connection: FakeAnalyticsConnection,
    *,
    fail_connection: bool = False,
) -> tuple[
    QueryExecutor,
    FakeAnalyticsPool,
]:
    pool = FakeAnalyticsPool(
        connection,
        fail_connection=fail_connection,
    )
    clock_values = iter(
        (
            10.0,
            10.025,
        )
    )
    executor = QueryExecutor(
        lambda: cast(
            RuntimeConnectionPool,
            pool,
        ),
        statement_timeout_ms=8000,
        lock_timeout_ms=2000,
        idle_in_transaction_session_timeout_ms=30000,
        query_timeout_seconds=10.0,
        clock=lambda: next(clock_values),
    )

    return (
        executor,
        pool,
    )


def test_query_executor_uses_read_only_transaction_and_normalizes_values() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    identifier = UUID("12345678-1234-5678-1234-567812345678")
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=(
            "region",
            "amount",
            "sale_date",
            "created_at",
            "sale_time",
            "identifier",
            "active",
            "optional",
        ),
        type_codes=(
            1043,
            1700,
            1082,
            1184,
            1083,
            2950,
            16,
            1043,
        ),
        rows=(
            (
                "North",
                Decimal("1250.50"),
                date(
                    2026,
                    8,
                    23,
                ),
                datetime(
                    2026,
                    8,
                    23,
                    12,
                    30,
                    tzinfo=UTC,
                ),
                time(
                    12,
                    30,
                ),
                identifier,
                True,
                None,
            ),
        ),
    )
    executor, pool = build_executor(connection)

    result = executor.execute(generation)

    assert result.rows == (
        (
            "North",
            "1250.50",
            "2026-08-23",
            "2026-08-23T12:30:00+00:00",
            "12:30:00",
            str(identifier),
            True,
            None,
        ),
    )
    assert result.row_count == 1
    assert result.execution_time_ms == pytest.approx(25.0)
    assert result.transaction_read_only is True
    assert tuple(
        (
            metadata.name,
            metadata.postgres_type_code,
            metadata.value_kind,
        )
        for metadata in result.internal_column_metadata
    ) == (
        ("region", 1043, "text"),
        ("amount", 1700, "number"),
        ("sale_date", 1082, "date"),
        ("created_at", 1184, "datetime"),
        ("sale_time", 1083, "time"),
        ("identifier", 2950, "uuid"),
        ("active", 16, "boolean"),
        ("optional", 1043, "text"),
    )
    assert "internal_column_metadata" not in result.model_dump(
        mode="json",
    )
    assert (
        "internal_column_metadata"
        not in QueryExecutionResult.model_json_schema(
            mode="serialization",
        )["properties"]
    )
    assert pool.timeouts == [
        10.0,
    ]
    assert connection.fetchmany_sizes == [2]
    assert connection.fetchall_calls == 0
    assert connection.events == [
        "transaction:begin",
        "transaction:commit",
    ]

    statements = tuple(statement for statement, parameters in connection.statements)

    assert statements == (
        "SET TRANSACTION READ ONLY",
        (
            "SELECT "
            "set_config('statement_timeout', %s, true), "
            "set_config('lock_timeout', %s, true), "
            "set_config('idle_in_transaction_session_timeout', %s, true)"
        ),
        (
            "SELECT "
            "current_setting('transaction_read_only'), "
            "current_setting('statement_timeout'), "
            "current_setting('lock_timeout'), "
            "current_setting('idle_in_transaction_session_timeout')"
        ),
        generation.validated_sql.sql,
    )
    assert connection.statements[1][1] == (
        "8000ms",
        "2000ms",
        "30000ms",
    )


def test_query_executor_marks_unknown_postgres_type() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("custom_value",),
        type_codes=(999_999,),
        rows=(("controlled",),),
    )
    executor, _ = build_executor(connection)

    result = executor.execute(generation)

    assert result.internal_column_metadata == (
        QueryResultColumnMetadata(
            name="custom_value",
            postgres_type_code=999_999,
            value_kind="unknown",
        ),
    )


def test_query_executor_rejects_invalid_column_metadata() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("region",),
        type_codes=(0,),
        rows=(("North",),),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionResultError,
        match="metadata is unavailable",
    ):
        executor.execute(generation)

    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_rejects_non_read_only_runtime() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("region",),
        rows=(("North",),),
        read_only="off",
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionSecurityError,
        match="not read-only",
    ):
        executor.execute(generation)

    assert generation.validated_sql.sql not in {
        statement for statement, parameters in connection.statements
    }
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_sanitizes_connection_failure() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("region",),
        rows=(),
    )
    executor, _ = build_executor(
        connection,
        fail_connection=True,
    )

    with pytest.raises(
        QueryExecutionUnavailableError,
        match="execution is unavailable",
    ) as captured:
        executor.execute(generation)

    assert "sensitive connection failure" not in str(captured.value)


def test_query_executor_sanitizes_query_failure() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("region",),
        rows=(),
        fail_query=True,
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionUnavailableError,
        match="execution is unavailable",
    ) as captured:
        executor.execute(generation)

    assert "sensitive database failure" not in str(captured.value)
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_rejects_duplicate_columns() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=(
            "Region",
            "region",
        ),
        rows=(
            (
                "North",
                "North",
            ),
        ),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionResultError,
        match="controlled validation",
    ):
        executor.execute(generation)


def test_query_executor_rejects_unsupported_database_values() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("binary_value",),
        rows=((b"not-json-safe",),),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionResultError,
        match="unsupported value",
    ):
        executor.execute(generation)


def test_query_executor_rejects_rows_above_validated_limit() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("region",),
        rows=(
            ("North",),
            ("South",),
        ),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionResultError,
        match="exceeds the validated row limit",
    ):
        executor.execute(generation)

    assert connection.fetchmany_sizes == [2]
    assert connection.fetchall_calls == 0
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_factory_uses_settings() -> None:
    generation = build_generation_result(
        row_limit=1,
    )
    connection = FakeAnalyticsConnection(
        generated_sql=(generation.validated_sql.sql),
        columns=("region",),
        rows=(),
    )
    pool = FakeAnalyticsPool(connection)
    runtime_pool = cast(
        RuntimeConnectionPool,
        pool,
    )
    database_pools = DatabasePools(
        application=runtime_pool,
        analytics=runtime_pool,
        open_timeout_seconds=5.0,
    )
    settings = Settings.model_validate(
        {
            "statement_timeout_ms": 2500,
            "lock_timeout_ms": 1250,
            "idle_in_transaction_session_timeout_ms": 15000,
            "query_timeout_seconds": 4.5,
        }
    )

    executor = create_query_executor(
        settings,
        database_pools,
    )

    assert executor.statement_timeout_ms == 2500
    assert executor.lock_timeout_ms == 1250
    assert executor.idle_in_transaction_session_timeout_ms == 15000
    assert executor.query_timeout_seconds == 4.5


@pytest.mark.parametrize(
    (
        "statement_timeout_ms",
        "lock_timeout_ms",
        "idle_timeout_ms",
        "query_timeout_seconds",
        "expected_message",
    ),
    (
        (99, 2000, 30000, 10.0, "statement_timeout_ms"),
        (300_001, 2000, 30000, 10.0, "statement_timeout_ms"),
        (8000, 0, 30000, 10.0, "lock_timeout_ms"),
        (8000, 300_001, 30000, 10.0, "lock_timeout_ms"),
        (
            8000,
            2000,
            99,
            10.0,
            "idle_in_transaction_session_timeout_ms",
        ),
        (
            8000,
            2000,
            3_600_001,
            10.0,
            "idle_in_transaction_session_timeout_ms",
        ),
        (8000, 2000, 30000, 0.0, "query_timeout_seconds"),
        (8000, 2000, 30000, 300.1, "query_timeout_seconds"),
    ),
)
def test_query_executor_rejects_invalid_timeout_policies(
    statement_timeout_ms: int,
    lock_timeout_ms: int,
    idle_timeout_ms: int,
    query_timeout_seconds: float,
    expected_message: str,
) -> None:
    def pool_provider() -> RuntimeConnectionPool:
        return cast(
            RuntimeConnectionPool,
            object(),
        )

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        QueryExecutor(
            pool_provider,
            statement_timeout_ms=statement_timeout_ms,
            lock_timeout_ms=lock_timeout_ms,
            idle_in_transaction_session_timeout_ms=(idle_timeout_ms),
            query_timeout_seconds=query_timeout_seconds,
        )


@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
    ),
    (
        ("lock_timeout_ms", 0),
        ("lock_timeout_ms", 300_001),
        ("idle_in_transaction_session_timeout_ms", 99),
        ("idle_in_transaction_session_timeout_ms", 3_600_001),
    ),
)
def test_settings_reject_invalid_executor_timeout_policies(
    field_name: str,
    invalid_value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                field_name: invalid_value,
            }
        )


def test_executor_timeout_settings_have_controlled_defaults() -> None:
    settings = Settings.model_validate({})

    assert settings.statement_timeout_ms == 8000
    assert settings.lock_timeout_ms == 2000
    assert settings.idle_in_transaction_session_timeout_ms == 30000
    assert settings.query_timeout_seconds == 10.0


@pytest.mark.parametrize(
    "active_timeouts",
    (
        ("9s", "2s", "30s"),
        ("8s", "3s", "30s"),
        ("8s", "2s", "31s"),
    ),
)
def test_query_executor_rejects_mismatched_runtime_timeouts(
    active_timeouts: tuple[object, ...],
) -> None:
    generation = build_generation_result(row_limit=1)
    connection = FakeAnalyticsConnection(
        generated_sql=generation.validated_sql.sql,
        columns=("id",),
        rows=((1,),),
        type_codes=(23,),
        active_timeouts=active_timeouts,
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionSecurityError,
        match="timeout settings are not active",
    ):
        executor.execute(generation)

    assert generation.validated_sql.sql not in {statement for statement, _ in connection.statements}
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_rejects_missing_configured_timeout_values() -> None:
    generation = build_generation_result(row_limit=1)
    connection = FakeAnalyticsConnection(
        generated_sql=generation.validated_sql.sql,
        columns=("id",),
        rows=((1,),),
        type_codes=(23,),
        configured_timeouts=(),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionSecurityError,
        match="timeout settings are unavailable",
    ):
        executor.execute(generation)

    assert generation.validated_sql.sql not in {statement for statement, _ in connection.statements}
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_rejects_malformed_active_security_settings() -> None:
    generation = build_generation_result(row_limit=1)
    connection = FakeAnalyticsConnection(
        generated_sql=generation.validated_sql.sql,
        columns=("id",),
        rows=((1,),),
        type_codes=(23,),
        active_timeouts=(),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionSecurityError,
        match="security settings are unavailable",
    ):
        executor.execute(generation)

    assert generation.validated_sql.sql not in {statement for statement, _ in connection.statements}
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_sanitizes_timeout_setup_failure() -> None:
    generation = build_generation_result(row_limit=1)
    sensitive = "sensitive timeout configuration failure"
    connection = FakeAnalyticsConnection(
        generated_sql=generation.validated_sql.sql,
        columns=("id",),
        rows=((1,),),
        type_codes=(23,),
        fail_timeout_setup=True,
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionUnavailableError,
        match="execution is unavailable",
    ) as captured:
        executor.execute(generation)

    assert sensitive not in str(captured.value)
    assert generation.validated_sql.sql not in {statement for statement, _ in connection.statements}
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_rejects_missing_result_set_inside_transaction() -> None:
    generation = build_generation_result(row_limit=1)
    connection = FakeAnalyticsConnection(
        generated_sql="SELECT different_controlled_statement",
        columns=("id",),
        rows=(),
        type_codes=(23,),
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionResultError,
        match="did not return a result set",
    ):
        executor.execute(generation)

    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


@pytest.mark.parametrize(
    (
        "columns",
        "rows",
        "type_codes",
        "expected_message",
    ),
    (
        (
            ("ID", "id"),
            ((1, 1),),
            (23, 23),
            "controlled validation",
        ),
        (
            ("id", "name"),
            ((1,),),
            (23, 1043),
            "controlled validation",
        ),
        (
            ("payload",),
            ((b"not-json-safe",),),
            (17,),
            "unsupported value",
        ),
    ),
)
def test_query_executor_rolls_back_result_validation_failures(
    columns: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
    type_codes: tuple[int, ...],
    expected_message: str,
) -> None:
    generation = build_generation_result(row_limit=1)
    connection = FakeAnalyticsConnection(
        generated_sql=generation.validated_sql.sql,
        columns=columns,
        rows=rows,
        type_codes=type_codes,
    )
    executor, _ = build_executor(connection)

    with pytest.raises(
        QueryExecutionResultError,
        match=expected_message,
    ):
        executor.execute(generation)

    assert connection.fetchmany_sizes == [2]
    assert connection.fetchall_calls == 0
    assert connection.events == [
        "transaction:begin",
        "transaction:rollback",
    ]


def test_query_executor_factory_uses_only_analytics_pool() -> None:
    generation = build_generation_result(row_limit=1)
    connection = FakeAnalyticsConnection(
        generated_sql=generation.validated_sql.sql,
        columns=("id",),
        rows=((1,),),
        type_codes=(23,),
        configured_timeouts=(
            "2500ms",
            "1250ms",
            "15000ms",
        ),
    )
    analytics_pool = FakeAnalyticsPool(connection)
    database_pools = DatabasePools(
        application=cast(
            RuntimeConnectionPool,
            object(),
        ),
        analytics=cast(
            RuntimeConnectionPool,
            analytics_pool,
        ),
        open_timeout_seconds=5.0,
    )
    settings = Settings.model_validate(
        {
            "statement_timeout_ms": 2500,
            "lock_timeout_ms": 1250,
            "idle_in_transaction_session_timeout_ms": 15000,
            "query_timeout_seconds": 4.5,
        }
    )
    executor = create_query_executor(
        settings,
        database_pools,
    )

    result = executor.execute(generation)

    assert result.row_count == 1
    assert analytics_pool.timeouts == [4.5]
    assert connection.fetchmany_sizes == [2]
    assert connection.fetchall_calls == 0
    assert connection.statements[1][1] == (
        "2500ms",
        "1250ms",
        "15000ms",
    )
