from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from math import isfinite
from time import perf_counter
from uuid import UUID

from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.db.pools import (
    DatabasePools,
    RuntimeConnectionPool,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
    QueryResultRow,
    QueryResultValue,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)


class QueryExecutionError(RuntimeError):
    """Base error raised by controlled query execution."""


class QueryExecutionSecurityError(QueryExecutionError):
    """Raised when database security invariants are not active."""


class QueryExecutionResultError(QueryExecutionError):
    """Raised when database output violates the result contract."""


class QueryExecutionUnavailableError(QueryExecutionError):
    """Raised when the analytics database cannot execute a query."""


def normalize_query_value(
    value: object,
) -> QueryResultValue:
    """Convert supported PostgreSQL values into strict JSON primitives."""
    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ):
        return value

    if isinstance(
        value,
        (
            str,
            int,
        ),
    ):
        return value

    if isinstance(
        value,
        float,
    ):
        if not isfinite(value):
            raise QueryExecutionResultError("Query result contains an unsupported value")

        return value

    if isinstance(
        value,
        Decimal,
    ):
        if not value.is_finite():
            raise QueryExecutionResultError("Query result contains an unsupported value")

        return str(value)

    if isinstance(
        value,
        (
            datetime,
            date,
            time,
        ),
    ):
        return value.isoformat()

    if isinstance(
        value,
        UUID,
    ):
        return str(value)

    raise QueryExecutionResultError("Query result contains an unsupported value")


class QueryExecutor:
    """Execute validated SQL through the managed analytics pool."""

    def __init__(
        self,
        analytics_pool_provider: Callable[
            [],
            RuntimeConnectionPool,
        ],
        *,
        statement_timeout_ms: int,
        query_timeout_seconds: float,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        if not 100 <= statement_timeout_ms <= 300_000:
            raise ValueError("statement_timeout_ms must be between 100 and 300000")

        if not 0.0 < query_timeout_seconds <= 300.0:
            raise ValueError("query_timeout_seconds must be between 0 and 300")

        self._analytics_pool_provider = analytics_pool_provider
        self._statement_timeout_ms = statement_timeout_ms
        self._query_timeout_seconds = query_timeout_seconds
        self._clock = clock

    @property
    def statement_timeout_ms(self) -> int:
        return self._statement_timeout_ms

    @property
    def query_timeout_seconds(self) -> float:
        return self._query_timeout_seconds

    def execute(
        self,
        generation: SQLGenerationResult,
    ) -> QueryExecutionResult:
        """Execute one validated query without permitting database writes."""
        started_at = self._clock()

        try:
            analytics_pool = self._analytics_pool_provider()

            with (
                analytics_pool.connection(
                    timeout=self._query_timeout_seconds,
                ) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    ("SELECT set_config('statement_timeout', %s, true)"),
                    (f"{self._statement_timeout_ms}ms",),
                )
                cursor.execute("SELECT current_setting('transaction_read_only')")
                read_only_row = cursor.fetchone()

                if read_only_row is None or str(read_only_row[0]) != "on":
                    raise QueryExecutionSecurityError("Analytics transaction is not read-only")

                cursor.execute(generation.validated_sql.sql)
                description = cursor.description

                if description is None:
                    raise QueryExecutionResultError("Query did not return a result set")

                columns = tuple(column.name for column in description)
                raw_rows = cursor.fetchall()

            rows: tuple[QueryResultRow, ...] = tuple(
                tuple(normalize_query_value(value) for value in raw_row) for raw_row in raw_rows
            )
            execution_time_ms = max(
                0.0,
                (self._clock() - started_at) * 1000.0,
            )

            try:
                return QueryExecutionResult(
                    generation=generation,
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    statement_timeout_ms=(self._statement_timeout_ms),
                    query_timeout_seconds=(self._query_timeout_seconds),
                    execution_time_ms=execution_time_ms,
                )
            except ValidationError:
                raise QueryExecutionResultError(
                    "Query result failed controlled validation"
                ) from None
        except QueryExecutionError:
            raise
        except Exception as error:
            raise QueryExecutionUnavailableError("Query execution is unavailable") from error


type QueryExecutorFactory = Callable[
    [
        Settings,
        DatabasePools,
    ],
    QueryExecutor,
]


def create_query_executor(
    settings: Settings,
    database_pools: DatabasePools,
) -> QueryExecutor:
    """Create the managed analytics query executor."""

    def provide_analytics_pool() -> RuntimeConnectionPool:
        return database_pools.analytics

    return QueryExecutor(
        provide_analytics_pool,
        statement_timeout_ms=(settings.statement_timeout_ms),
        query_timeout_seconds=(settings.query_timeout_seconds),
    )
