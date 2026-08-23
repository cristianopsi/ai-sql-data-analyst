from collections.abc import Callable
from typing import Never

from sqlglot import (
    exp,
    parse,
)
from sqlglot.errors import ParseError

from backend.app.core.config import Settings
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.schemas.sql_generation import (
    SQLProposal,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)

ALLOWED_FUNCTION_NAMES = frozenset(
    {
        "ABS",
        "AVG",
        "CAST",
        "CEIL",
        "COALESCE",
        "COUNT",
        "CURRENT_DATE",
        "CURRENT_TIMESTAMP",
        "DATE_TRUNC",
        "EXTRACT",
        "FLOOR",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "ROUND",
        "SUM",
        "TIMESTAMP_TRUNC",
        "UPPER",
    }
)

PROHIBITED_NODE_TYPES: tuple[
    type[exp.Expression],
    ...,
] = (
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.CTE,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Into,
    exp.Lock,
    exp.Merge,
    exp.Star,
    exp.Subquery,
    exp.Union,
    exp.Update,
)

COMMENT_MARKERS = (
    "--",
    "/*",
    "*/",
)


class SQLValidationError(ValueError):
    """Raised when a SQL proposal fails closed validation."""


def _reject() -> Never:
    raise SQLValidationError("SQL proposal failed security validation")


def _parse_select(
    sql: str,
) -> exp.Select:
    if any(marker in sql for marker in COMMENT_MARKERS):
        _reject()

    try:
        parsed_expressions = parse(
            sql,
            read="postgres",
        )
    except (
        ParseError,
        ValueError,
    ):
        _reject()

    expressions = tuple(expression for expression in parsed_expressions if expression is not None)

    if len(expressions) != 1:
        _reject()

    expression = expressions[0]

    if not isinstance(
        expression,
        exp.Select,
    ):
        _reject()

    select_count = sum(isinstance(node, exp.Select) for node in expression.walk())

    if select_count != 1:
        _reject()

    if any(
        isinstance(
            node,
            PROHIBITED_NODE_TYPES,
        )
        for node in expression.walk()
    ):
        _reject()

    return expression


def _validate_functions(
    expression: exp.Select,
) -> None:
    for function in expression.find_all(exp.Func):
        function_name = function.sql_name().upper()

        if function_name not in ALLOWED_FUNCTION_NAMES:
            _reject()


def _allowed_columns(
    context: CompactGroundingContext,
) -> dict[str, frozenset[str]]:
    return {
        table.name.casefold(): frozenset(column.name.casefold() for column in table.columns)
        for table in context.tables
    }


def _validate_tables(
    expression: exp.Select,
    context: CompactGroundingContext,
) -> tuple[
    dict[str, str],
    tuple[str, ...],
]:
    tables_by_name = {table.name.casefold(): table for table in context.tables}
    aliases: dict[str, str] = {}
    referenced_tables: set[str] = set()

    for table_expression in expression.find_all(exp.Table):
        table_name = table_expression.name.casefold()
        table_contract = tables_by_name.get(table_name)

        if table_contract is None:
            _reject()

        if table_expression.catalog:
            _reject()

        schema_name = table_expression.db.casefold() if table_expression.db else ""

        if schema_name and schema_name != table_contract.schema_name.casefold():
            _reject()

        alias_name = table_expression.alias_or_name.casefold()
        current_alias_target = aliases.get(alias_name)

        if current_alias_target is not None and current_alias_target != table_name:
            _reject()

        aliases[alias_name] = table_name
        aliases[table_name] = table_name
        referenced_tables.add(table_name)

    if not referenced_tables:
        _reject()

    return (
        aliases,
        tuple(sorted(referenced_tables)),
    )


def _select_aliases(
    expression: exp.Select,
) -> frozenset[str]:
    return frozenset(
        alias
        for selected_expression in (expression.expressions)
        if (alias := (selected_expression.alias.casefold()))
    )


def _validate_columns(
    expression: exp.Select,
    context: CompactGroundingContext,
    aliases: dict[str, str],
) -> tuple[str, ...]:
    allowed = _allowed_columns(context)
    select_aliases = _select_aliases(expression)
    referenced_columns: set[str] = set()

    for column_expression in expression.find_all(exp.Column):
        if column_expression.catalog or column_expression.db:
            _reject()

        column_name = column_expression.name.casefold()
        qualifier = column_expression.table.casefold() if column_expression.table else ""

        if qualifier:
            table_name = aliases.get(qualifier)

            if table_name is None:
                _reject()

            if column_name not in allowed[table_name]:
                _reject()

            referenced_columns.add(f"{table_name}.{column_name}")
            continue

        if column_name in select_aliases:
            continue

        candidate_tables = tuple(
            table_name for table_name in set(aliases.values()) if column_name in allowed[table_name]
        )

        if len(candidate_tables) != 1:
            _reject()

        referenced_columns.add(f"{candidate_tables[0]}.{column_name}")

    if not referenced_columns:
        _reject()

    return tuple(sorted(referenced_columns))


def _apply_row_limit(
    expression: exp.Select,
    max_result_rows: int,
) -> tuple[
    exp.Select,
    int,
]:
    validated_expression = expression.copy()
    limit_node = validated_expression.args.get("limit")

    if limit_node is None:
        effective_limit = max_result_rows
    else:
        if not isinstance(
            limit_node,
            exp.Limit,
        ):
            _reject()

        limit_expression = limit_node.args.get("expression")

        if not isinstance(
            limit_expression,
            exp.Literal,
        ):
            _reject()

        literal_value = limit_expression.this

        if (
            not isinstance(
                literal_value,
                str,
            )
            or not literal_value.isdigit()
        ):
            _reject()

        requested_limit = int(literal_value)

        if requested_limit < 1:
            _reject()

        effective_limit = min(
            requested_limit,
            max_result_rows,
        )

    validated_expression.set(
        "limit",
        exp.Limit(expression=exp.Literal.number(effective_limit)),
    )

    return (
        validated_expression,
        effective_limit,
    )


def validate_sql_proposal(
    proposal: SQLProposal,
    context: CompactGroundingContext,
    *,
    max_result_rows: int,
) -> ValidatedSQL:
    """Validate and canonicalize one SQL proposal."""
    if max_result_rows < 1:
        raise ValueError("max_result_rows must be positive")

    if context.grounding_status != "grounded":
        _reject()

    if (
        proposal.context_version != context.context_version
        or proposal.semantic_version != context.semantic_version
        or proposal.catalog_version != context.catalog_version
    ):
        _reject()

    expression = _parse_select(proposal.sql)
    _validate_functions(expression)

    (
        aliases,
        referenced_tables,
    ) = _validate_tables(
        expression,
        context,
    )

    referenced_columns = _validate_columns(
        expression,
        context,
        aliases,
    )

    (
        limited_expression,
        effective_limit,
    ) = _apply_row_limit(
        expression,
        max_result_rows,
    )

    canonical_sql = limited_expression.sql(
        dialect="postgres",
        pretty=False,
    )

    return ValidatedSQL(
        proposal_version=(proposal.proposal_version),
        context_version=(context.context_version),
        semantic_version=(context.semantic_version),
        catalog_version=(context.catalog_version),
        provider=proposal.provider,
        model=proposal.model,
        sql=canonical_sql,
        explanation=proposal.explanation,
        row_limit=effective_limit,
        referenced_tables=referenced_tables,
        referenced_columns=(referenced_columns),
        usage=proposal.usage,
    )


class SQLValidator:
    """Validate SQL proposals against safe context."""

    def __init__(
        self,
        *,
        max_result_rows: int,
    ) -> None:
        if max_result_rows < 1:
            raise ValueError("max_result_rows must be positive")

        self._max_result_rows = max_result_rows

    @property
    def max_result_rows(self) -> int:
        return self._max_result_rows

    def validate(
        self,
        proposal: SQLProposal,
        context: CompactGroundingContext,
    ) -> ValidatedSQL:
        return validate_sql_proposal(
            proposal,
            context,
            max_result_rows=(self._max_result_rows),
        )


type SQLValidatorFactory = Callable[
    [Settings],
    SQLValidator,
]


def create_sql_validator(
    settings: Settings,
) -> SQLValidator:
    """Create the configured SQL validator."""
    return SQLValidator(
        max_result_rows=(settings.max_result_rows),
    )
