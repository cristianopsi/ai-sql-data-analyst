from collections.abc import Callable
from dataclasses import dataclass
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
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Into,
    exp.Lock,
    exp.Offset,
    exp.Merge,
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


MAX_CTE_COUNT = 2


@dataclass(frozen=True)
class _CTEContract:
    name: str
    columns: frozenset[str]
    lineage: dict[
        str,
        tuple[str, str] | None,
    ]
    referenced_tables: tuple[str, ...]
    referenced_columns: tuple[str, ...]


def _top_level_ctes(
    expression: exp.Select,
) -> tuple[exp.CTE, ...]:
    with_clause = expression.args.get("with_")

    if with_clause is None:
        return ()

    if not isinstance(with_clause, exp.With):
        _reject()

    if with_clause.args.get("recursive"):
        _reject()

    ctes = tuple(with_clause.expressions)

    if not ctes or len(ctes) > MAX_CTE_COUNT:
        _reject()

    cte_names: set[str] = set()

    for cte in ctes:
        if not isinstance(cte, exp.CTE):
            _reject()

        cte_name = cte.alias_or_name.casefold()

        if not cte_name or cte_name in cte_names:
            _reject()

        cte_names.add(cte_name)

        alias_expression = cte.args.get("alias")

        if not isinstance(alias_expression, exp.TableAlias):
            _reject()

        if alias_expression.args.get("columns"):
            _reject()

        if cte.args.get("materialized") is not None:
            _reject()

        body = cte.this

        if not isinstance(body, exp.Select):
            _reject()

        if body.args.get("with_") is not None:
            _reject()

        if body.args.get("limit") is not None:
            _reject()

    return ctes


def _has_controlled_count_star(
    expression: exp.Select,
) -> bool:
    return any(
        isinstance(count_expression.this, exp.Star)
        for count_expression in expression.find_all(exp.Count)
    )


def _validate_star_usage(
    expression: exp.Select,
) -> None:
    for star in expression.find_all(exp.Star):
        parent = star.parent

        if not isinstance(parent, exp.Count) or parent.this is not star:
            _reject()

        ancestor = parent.parent

        while ancestor is not None:
            if isinstance(ancestor, exp.Window):
                _reject()

            if isinstance(ancestor, exp.Select):
                break

            ancestor = ancestor.parent


def _validate_count_usage(
    expression: exp.Select,
) -> None:
    for count_expression in expression.find_all(exp.Count):
        argument = count_expression.this

        if argument is None:
            _reject()

        if isinstance(argument, exp.Literal):
            _reject()


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

    if not isinstance(expression, exp.Select):
        _reject()

    ctes = _top_level_ctes(expression)
    select_count = sum(isinstance(node, exp.Select) for node in expression.walk())

    if select_count != 1 + len(ctes):
        _reject()

    if any(
        isinstance(
            node,
            PROHIBITED_NODE_TYPES,
        )
        for node in expression.walk()
    ):
        _reject()

    _validate_star_usage(expression)
    _validate_count_usage(expression)

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


type RelationshipEndpoint = tuple[str, str]
type RelationshipEdge = frozenset[RelationshipEndpoint]


def _relationship_edges(
    context: CompactGroundingContext,
) -> frozenset[RelationshipEdge]:
    return frozenset(
        frozenset(
            (
                (
                    relationship.from_column.table_name.casefold(),
                    relationship.from_column.column_name.casefold(),
                ),
                (
                    relationship.to_column.table_name.casefold(),
                    relationship.to_column.column_name.casefold(),
                ),
            )
        )
        for relationship in context.relationships
    )


def _join_modifier(
    join: exp.Join,
    key: str,
) -> str:
    value = join.args.get(key)

    if value is None:
        return ""

    return str(value).upper()


def _join_endpoint(
    column: exp.Column,
    aliases: dict[str, str],
) -> RelationshipEndpoint:
    if column.catalog or column.db or not column.table:
        _reject()

    table_name = aliases.get(column.table.casefold())

    if table_name is None:
        _reject()

    return (
        table_name,
        column.name.casefold(),
    )


def _resolve_join_endpoint(
    column: exp.Column,
    aliases: dict[str, str],
    virtual_aliases: dict[str, str],
    cte_contracts: dict[str, _CTEContract],
) -> tuple[str, str]:
    qualifier = column.table.casefold()

    if qualifier in aliases:
        return _join_endpoint(
            column,
            aliases,
        )

    cte_name = virtual_aliases.get(qualifier)

    if cte_name is None:
        _reject()

    contract = cte_contracts.get(cte_name)

    if contract is None:
        _reject()

    endpoint = contract.lineage.get(column.name.casefold())

    if endpoint is None:
        _reject()

    return endpoint


def _validate_joins(
    expression: exp.Select,
    context: CompactGroundingContext,
    aliases: dict[str, str],
    *,
    virtual_aliases: dict[str, str] | None = None,
    cte_contracts: dict[str, _CTEContract] | None = None,
) -> None:
    joins = expression.args.get("joins") or ()

    if not joins:
        return

    active_virtual_aliases = virtual_aliases or {}
    active_cte_contracts = cte_contracts or {}
    from_clause = expression.args.get("from_")

    if not isinstance(from_clause, exp.From) or not isinstance(from_clause.this, exp.Table):
        _reject()

    introduced_aliases = {from_clause.this.alias_or_name.casefold()}
    allowed_edges = _relationship_edges(context)
    allowed_join_shapes = {
        ("", ""),
        ("", "INNER"),
        ("LEFT", ""),
        ("LEFT", "OUTER"),
    }

    for join in joins:
        if not isinstance(join, exp.Join):
            _reject()

        target = join.this

        if not isinstance(target, exp.Table):
            _reject()

        if _join_modifier(join, "method"):
            _reject()

        join_shape = (
            _join_modifier(join, "side"),
            _join_modifier(join, "kind"),
        )

        if join_shape not in allowed_join_shapes:
            _reject()

        target_alias = target.alias_or_name.casefold()

        if target_alias in introduced_aliases:
            _reject()

        on_expression = join.args.get("on")

        if not isinstance(on_expression, exp.EQ):
            _reject()

        left_expression = on_expression.this
        right_expression = on_expression.expression

        if not isinstance(left_expression, exp.Column) or not isinstance(
            right_expression, exp.Column
        ):
            _reject()

        left_alias = left_expression.table.casefold()
        right_alias = right_expression.table.casefold()

        if not left_alias or not right_alias:
            _reject()

        if target_alias == left_alias:
            other_alias = right_alias
        elif target_alias == right_alias:
            other_alias = left_alias
        else:
            _reject()

        if other_alias not in introduced_aliases or other_alias == target_alias:
            _reject()

        current_edge: RelationshipEdge = frozenset(
            (
                _resolve_join_endpoint(
                    left_expression,
                    aliases,
                    active_virtual_aliases,
                    active_cte_contracts,
                ),
                _resolve_join_endpoint(
                    right_expression,
                    aliases,
                    active_virtual_aliases,
                    active_cte_contracts,
                ),
            )
        )

        if current_edge not in allowed_edges:
            _reject()

        introduced_aliases.add(target_alias)


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
    *,
    virtual_aliases: dict[str, str] | None = None,
    cte_contracts: dict[str, _CTEContract] | None = None,
) -> tuple[str, ...]:
    allowed = _allowed_columns(context)
    select_aliases = _select_aliases(expression)
    active_virtual_aliases = virtual_aliases or {}
    active_cte_contracts = cte_contracts or {}
    referenced_columns: set[str] = set()
    validated_reference = False

    for column_expression in expression.find_all(exp.Column):
        if column_expression.catalog or column_expression.db:
            _reject()

        column_name = column_expression.name.casefold()
        qualifier = column_expression.table.casefold() if column_expression.table else ""

        if qualifier:
            table_name = aliases.get(qualifier)

            if table_name is not None:
                if column_name not in allowed[table_name]:
                    _reject()

                referenced_columns.add(f"{table_name}.{column_name}")
                validated_reference = True
                continue

            cte_name = active_virtual_aliases.get(qualifier)

            if cte_name is None:
                _reject()

            contract = active_cte_contracts.get(cte_name)

            if contract is None or column_name not in contract.columns:
                _reject()

            endpoint = contract.lineage.get(column_name)

            if endpoint is not None:
                referenced_columns.add(f"{endpoint[0]}.{endpoint[1]}")

            validated_reference = True
            continue

        if column_name in select_aliases:
            continue

        candidate_sources: list[tuple[str, str]] = []

        for table_name in set(aliases.values()):
            if column_name in allowed[table_name]:
                candidate_sources.append(("physical", table_name))

        for cte_name in set(active_virtual_aliases.values()):
            contract = active_cte_contracts.get(cte_name)

            if contract is not None and column_name in contract.columns:
                candidate_sources.append(("cte", cte_name))

        if len(candidate_sources) != 1:
            _reject()

        source_kind, source_name = candidate_sources[0]

        if source_kind == "physical":
            referenced_columns.add(f"{source_name}.{column_name}")
        else:
            contract = active_cte_contracts[source_name]
            endpoint = contract.lineage.get(column_name)

            if endpoint is not None:
                referenced_columns.add(f"{endpoint[0]}.{endpoint[1]}")

        validated_reference = True

    if not validated_reference and not _has_controlled_count_star(expression):
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


def _physical_column_endpoint(
    column: exp.Column,
    context: CompactGroundingContext,
    aliases: dict[str, str],
) -> tuple[str, str]:
    allowed = _allowed_columns(context)
    column_name = column.name.casefold()
    qualifier = column.table.casefold() if column.table else ""

    if qualifier:
        table_name = aliases.get(qualifier)

        if table_name is None or column_name not in allowed[table_name]:
            _reject()

        return (
            table_name,
            column_name,
        )

    candidate_tables = tuple(
        table_name for table_name in set(aliases.values()) if column_name in allowed[table_name]
    )

    if len(candidate_tables) != 1:
        _reject()

    return (
        candidate_tables[0],
        column_name,
    )


def _build_cte_contracts(
    expression: exp.Select,
    context: CompactGroundingContext,
) -> dict[str, _CTEContract]:
    physical_table_names = {table.name.casefold() for table in context.tables}
    contracts: dict[str, _CTEContract] = {}

    for cte in _top_level_ctes(expression):
        cte_name = cte.alias_or_name.casefold()

        if cte_name in physical_table_names or cte_name in contracts:
            _reject()

        body = cte.this

        if not isinstance(body, exp.Select):
            _reject()

        (
            aliases,
            referenced_tables,
        ) = _validate_tables(
            body,
            context,
        )

        _validate_joins(
            body,
            context,
            aliases,
        )

        referenced_columns = _validate_columns(
            body,
            context,
            aliases,
        )

        exported_columns: set[str] = set()
        lineage: dict[
            str,
            tuple[str, str] | None,
        ] = {}

        for selected_expression in body.expressions:
            output_name = selected_expression.alias_or_name.casefold()

            if not output_name or output_name in exported_columns:
                _reject()

            exported_columns.add(output_name)

            if isinstance(
                selected_expression,
                exp.Alias,
            ):
                selected_value = selected_expression.this
            elif isinstance(
                selected_expression,
                exp.Column,
            ):
                selected_value = selected_expression
            else:
                _reject()

            if isinstance(selected_value, exp.Column):
                lineage[output_name] = _physical_column_endpoint(
                    selected_value,
                    context,
                    aliases,
                )
            else:
                lineage[output_name] = None

        if not exported_columns:
            _reject()

        contracts[cte_name] = _CTEContract(
            name=cte_name,
            columns=frozenset(exported_columns),
            lineage=lineage,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
        )

    return contracts


def _validate_outer_sources(
    expression: exp.Select,
    context: CompactGroundingContext,
    cte_contracts: dict[str, _CTEContract],
) -> tuple[
    dict[str, str],
    dict[str, str],
    tuple[str, ...],
]:
    tables_by_name = {table.name.casefold(): table for table in context.tables}
    aliases: dict[str, str] = {}
    virtual_aliases: dict[str, str] = {}
    referenced_tables: set[str] = set()

    for table_expression in expression.find_all(exp.Table):
        table_name = table_expression.name.casefold()
        alias_name = table_expression.alias_or_name.casefold()

        if alias_name in aliases or alias_name in virtual_aliases:
            _reject()

        cte_contract = cte_contracts.get(table_name)

        if cte_contract is not None:
            if table_expression.catalog or table_expression.db:
                _reject()

            virtual_aliases[alias_name] = table_name
            continue

        table_contract = tables_by_name.get(table_name)

        if table_contract is None:
            _reject()

        if table_expression.catalog:
            _reject()

        schema_name = table_expression.db.casefold() if table_expression.db else ""

        if schema_name and schema_name != table_contract.schema_name.casefold():
            _reject()

        aliases[alias_name] = table_name
        referenced_tables.add(table_name)

    if not aliases and not virtual_aliases:
        _reject()

    return (
        aliases,
        virtual_aliases,
        tuple(sorted(referenced_tables)),
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
    ctes = _top_level_ctes(expression)

    if not ctes:
        (
            aliases,
            referenced_tables,
        ) = _validate_tables(
            expression,
            context,
        )

        _validate_joins(
            expression,
            context,
            aliases,
        )

        referenced_columns = _validate_columns(
            expression,
            context,
            aliases,
        )
    else:
        cte_contracts = _build_cte_contracts(
            expression,
            context,
        )
        outer_expression = expression.copy()
        outer_expression.set(
            "with_",
            None,
        )

        (
            aliases,
            virtual_aliases,
            outer_referenced_tables,
        ) = _validate_outer_sources(
            outer_expression,
            context,
            cte_contracts,
        )

        _validate_joins(
            outer_expression,
            context,
            aliases,
            virtual_aliases=virtual_aliases,
            cte_contracts=cte_contracts,
        )

        outer_referenced_columns = _validate_columns(
            outer_expression,
            context,
            aliases,
            virtual_aliases=virtual_aliases,
            cte_contracts=cte_contracts,
        )

        referenced_table_set = set(outer_referenced_tables)
        referenced_column_set = set(outer_referenced_columns)

        for contract in cte_contracts.values():
            referenced_table_set.update(contract.referenced_tables)
            referenced_column_set.update(contract.referenced_columns)

        referenced_tables = tuple(sorted(referenced_table_set))
        referenced_columns = tuple(sorted(referenced_column_set))

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
        proposal_version=proposal.proposal_version,
        context_version=context.context_version,
        semantic_version=context.semantic_version,
        catalog_version=context.catalog_version,
        provider=proposal.provider,
        model=proposal.model,
        sql=canonical_sql,
        explanation=proposal.explanation,
        row_limit=effective_limit,
        referenced_tables=referenced_tables,
        referenced_columns=referenced_columns,
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
