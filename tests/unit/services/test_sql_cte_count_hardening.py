import json

import pytest

from backend.app.schemas.llm import LLMTokenUsage
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.schemas.sql_generation import SQLProposal
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)
from backend.app.services.semantic_layer import (
    build_semantic_layer,
)
from backend.app.services.sql_validator import (
    SQLValidationError,
    SQLValidator,
)
from backend.app.services.text_to_sql import (
    SQL_PROPOSAL_SYSTEM_MESSAGE,
    SQL_REPAIR_SYSTEM_MESSAGE,
)

MAX_RESULT_ROWS = 25
SANITIZED_VALIDATION_ERROR = "SQL proposal failed security validation"


@pytest.fixture(scope="module")
def controlled_context() -> CompactGroundingContext:
    catalog = build_schema_catalog()
    semantic_layer = build_semantic_layer(catalog)

    return CompactGroundingContext(
        semantic_version="cte-count-test-semantic",
        catalog_version="cte-count-test-catalog",
        grounding_status="grounded",
        normalized_question=("validação permanente de CTE e contagem"),
        tables=tuple(catalog.tables),
        relationships=tuple(semantic_layer.relationships),
    )


@pytest.fixture(scope="module")
def validator() -> SQLValidator:
    return SQLValidator(
        max_result_rows=MAX_RESULT_ROWS,
    )


def build_proposal(
    context: CompactGroundingContext,
    sql: str,
) -> SQLProposal:
    return SQLProposal(
        context_version=context.context_version,
        semantic_version=context.semantic_version,
        catalog_version=context.catalog_version,
        provider="controlled-test",
        model="controlled-test",
        sql=sql,
        explanation="controlled permanent test",
        usage=LLMTokenUsage(
            input_tokens=1,
            output_tokens=1,
        ),
    )


def assert_sanitized_rejection(
    validator: SQLValidator,
    context: CompactGroundingContext,
    sql: str,
) -> None:
    with pytest.raises(
        SQLValidationError,
        match=("^SQL proposal failed security validation$"),
    ):
        validator.validate(
            build_proposal(
                context,
                sql,
            ),
            context,
        )


def test_count_star_has_controlled_empty_column_lineage(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            ("SELECT COUNT(*) AS total FROM retail.orders AS o"),
        ),
        controlled_context,
    )

    assert result.row_limit == MAX_RESULT_ROWS
    assert result.referenced_tables == ("orders",)
    assert result.referenced_columns == ()
    assert result.sql == ("SELECT COUNT(*) AS total FROM retail.orders AS o LIMIT 25")


@pytest.mark.parametrize(
    "sql,expected_tables,expected_columns",
    [
        (
            ("SELECT COUNT(*) FILTER (WHERE o.id IS NOT NULL) AS total FROM retail.orders AS o"),
            ("orders",),
            ("orders.id",),
        ),
        (
            ("SELECT COALESCE(COUNT(*), 0) AS total FROM retail.orders AS o"),
            ("orders",),
            (),
        ),
        (
            (
                "SELECT COUNT(*) AS total "
                "FROM retail.orders AS o "
                "JOIN retail.payments AS p "
                "ON p.order_id = o.id"
            ),
            (
                "orders",
                "payments",
            ),
            (
                "orders.id",
                "payments.order_id",
            ),
        ),
    ],
)
def test_controlled_count_star_shapes_are_accepted(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
    sql: str,
    expected_tables: tuple[str, ...],
    expected_columns: tuple[str, ...],
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            sql,
        ),
        controlled_context,
    )

    assert result.row_limit == MAX_RESULT_ROWS
    assert result.referenced_tables == expected_tables
    assert result.referenced_columns == expected_columns
    assert result.sql.upper().endswith("LIMIT 25")


@pytest.mark.parametrize(
    "sql",
    [
        ("SELECT COUNT(o.*) AS total FROM retail.orders AS o"),
        ("SELECT COUNT(DISTINCT *) AS total FROM retail.orders AS o"),
        ("SELECT COUNT(1) AS total FROM retail.orders AS o"),
        ("SELECT COUNT(*) OVER () AS total FROM retail.orders AS o"),
        ("SELECT SUM(*) AS total FROM retail.orders AS o"),
        ("SELECT MAX(*) AS total FROM retail.orders AS o"),
        "SELECT * FROM retail.orders AS o",
        "SELECT o.* FROM retail.orders AS o",
        "SELECT COUNT(*) AS total",
        (
            "SELECT COUNT(*) AS total "
            "FROM retail.orders AS o "
            "JOIN retail.payments AS p "
            "ON p.id = o.customer_id"
        ),
    ],
)
def test_uncontrolled_star_shapes_are_rejected(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
    sql: str,
) -> None:
    assert_sanitized_rejection(
        validator,
        controlled_context,
        sql,
    )


def test_simple_non_recursive_cte_is_accepted(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            (
                "WITH order_counts AS ("
                "SELECT o.region_id, "
                "COUNT(o.id) AS order_count "
                "FROM retail.orders AS o "
                "GROUP BY o.region_id"
                ") "
                "SELECT oc.region_id, oc.order_count "
                "FROM order_counts AS oc"
            ),
        ),
        controlled_context,
    )

    assert result.row_limit == MAX_RESULT_ROWS
    assert result.referenced_tables == ("orders",)
    assert result.referenced_columns == (
        "orders.id",
        "orders.region_id",
    )
    assert result.sql.startswith("WITH order_counts AS")
    assert result.sql.count("LIMIT 25") == 1


def test_count_star_inside_cte_is_accepted(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            (
                "WITH order_counts AS ("
                "SELECT o.region_id, "
                "COUNT(*) AS order_count "
                "FROM retail.orders AS o "
                "GROUP BY o.region_id"
                ") "
                "SELECT oc.region_id, oc.order_count "
                "FROM order_counts AS oc"
            ),
        ),
        controlled_context,
    )

    assert result.referenced_tables == ("orders",)
    assert result.referenced_columns == ("orders.region_id",)
    assert "COUNT(*) AS order_count" in result.sql
    assert result.sql.upper().endswith("LIMIT 25")


def test_cte_join_uses_physical_lineage(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            (
                "WITH payment_orders AS ("
                "SELECT p.order_id "
                "FROM retail.payments AS p"
                ") "
                "SELECT o.id, po.order_id "
                "FROM retail.orders AS o "
                "JOIN payment_orders AS po "
                "ON po.order_id = o.id"
            ),
        ),
        controlled_context,
    )

    assert result.referenced_tables == (
        "orders",
        "payments",
    )
    assert result.referenced_columns == (
        "orders.id",
        "payments.order_id",
    )


def test_two_independent_ctes_preserve_lineage(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            (
                "WITH order_ids AS ("
                "SELECT o.id FROM retail.orders AS o"
                "), payment_orders AS ("
                "SELECT p.order_id "
                "FROM retail.payments AS p"
                ") "
                "SELECT oi.id, po.order_id "
                "FROM order_ids AS oi "
                "JOIN payment_orders AS po "
                "ON po.order_id = oi.id"
            ),
        ),
        controlled_context,
    )

    assert result.referenced_tables == (
        "orders",
        "payments",
    )
    assert result.referenced_columns == (
        "orders.id",
        "payments.order_id",
    )
    assert result.sql.count(" AS (SELECT ") == 2


@pytest.mark.parametrize(
    "sql",
    [
        (
            "WITH first_cte AS ("
            "SELECT o.id FROM retail.orders AS o"
            "), second_cte AS ("
            "SELECT p.id FROM retail.payments AS p"
            "), third_cte AS ("
            "SELECT r.id FROM retail.regions AS r"
            ") "
            "SELECT f.id FROM first_cte AS f"
        ),
        (
            "WITH first_cte AS ("
            "SELECT o.id FROM retail.orders AS o"
            "), second_cte AS ("
            "SELECT f.id FROM first_cte AS f"
            ") "
            "SELECT s.id FROM second_cte AS s"
        ),
        (
            "WITH controlled(identifier) AS ("
            "SELECT o.id FROM retail.orders AS o"
            ") "
            "SELECT c.identifier "
            "FROM controlled AS c"
        ),
        (
            "WITH duplicated AS ("
            "SELECT o.id, o.customer_id AS id "
            "FROM retail.orders AS o"
            ") "
            "SELECT d.id FROM duplicated AS d"
        ),
        ("WITH orders AS (SELECT p.id FROM retail.payments AS p) SELECT orders.id FROM orders"),
        (
            "WITH duplicated AS ("
            "SELECT o.id FROM retail.orders AS o"
            "), duplicated AS ("
            "SELECT p.id FROM retail.payments AS p"
            ") "
            "SELECT d.id FROM duplicated AS d"
        ),
        (
            "WITH RECURSIVE sequence(n) AS ("
            "SELECT 1 "
            "UNION ALL "
            "SELECT n + 1 "
            "FROM sequence WHERE n < 10"
            ") "
            "SELECT sequence.n FROM sequence"
        ),
        (
            "WITH outer_cte AS ("
            "WITH inner_cte AS ("
            "SELECT o.id FROM retail.orders AS o"
            ") "
            "SELECT i.id FROM inner_cte AS i"
            ") "
            "SELECT oc.id FROM outer_cte AS oc"
        ),
        ("WITH deleted AS (DELETE FROM retail.orders RETURNING id) SELECT deleted.id FROM deleted"),
        (
            "WITH combined AS ("
            "SELECT o.id FROM retail.orders AS o "
            "UNION ALL "
            "SELECT p.id FROM retail.payments AS p"
            ") "
            "SELECT combined.id FROM combined"
        ),
        (
            "WITH unsafe AS ("
            "SELECT u.secret_value "
            "FROM retail.unknown_table AS u"
            ") "
            "SELECT unsafe.secret_value FROM unsafe"
        ),
        ("WITH unsafe AS (SELECT o.email FROM retail.orders AS o) SELECT unsafe.email FROM unsafe"),
        ("WITH unsafe AS (SELECT * FROM retail.orders AS o) SELECT unsafe.id FROM unsafe"),
        (
            "WITH controlled AS MATERIALIZED ("
            "SELECT o.id FROM retail.orders AS o"
            ") "
            "SELECT c.id FROM controlled AS c"
        ),
        (
            "WITH controlled AS NOT MATERIALIZED ("
            "SELECT o.id FROM retail.orders AS o"
            ") "
            "SELECT c.id FROM controlled AS c"
        ),
        (
            "WITH limited AS ("
            "SELECT o.id FROM retail.orders AS o "
            "LIMIT 10 OFFSET 1000000"
            ") "
            "SELECT limited.id FROM limited"
        ),
        (
            "WITH limited AS ("
            "SELECT o.id FROM retail.orders AS o "
            "LIMIT 10"
            ") "
            "SELECT limited.id FROM limited"
        ),
        (
            "WITH totals AS ("
            "SELECT o.region_id, COUNT(*) AS total "
            "FROM retail.orders AS o "
            "GROUP BY o.region_id"
            ") "
            "SELECT r.id, t.total "
            "FROM retail.regions AS r "
            "JOIN totals AS t ON t.total = r.id"
        ),
        (
            "WITH payment_rows AS ("
            "SELECT p.id, p.order_id "
            "FROM retail.payments AS p"
            ") "
            "SELECT o.id, pr.id "
            "FROM retail.orders AS o "
            "JOIN payment_rows AS pr "
            "ON pr.id = o.customer_id"
        ),
        (
            "WITH controlled AS ("
            "SELECT o.id FROM retail.orders AS o"
            ") "
            "SELECT c.customer_id "
            "FROM controlled AS c"
        ),
        (
            "WITH controlled AS ("
            "SELECT COUNT(*) "
            "FROM retail.orders AS o"
            ") "
            "SELECT c.total FROM controlled AS c"
        ),
        (
            "WITH payment_orders AS ("
            "SELECT p.order_id "
            "FROM retail.payments AS p"
            ") "
            "SELECT o.id, po.order_id "
            "FROM retail.orders AS o "
            "CROSS JOIN payment_orders AS po"
        ),
    ],
)
def test_unsafe_cte_shapes_are_rejected(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
    sql: str,
) -> None:
    assert_sanitized_rejection(
        validator,
        controlled_context,
        sql,
    )


def test_count_star_result_serializes_empty_references(
    validator: SQLValidator,
    controlled_context: CompactGroundingContext,
) -> None:
    result = validator.validate(
        build_proposal(
            controlled_context,
            ("SELECT COUNT(*) AS total FROM retail.orders AS o"),
        ),
        controlled_context,
    )
    serialized = json.loads(result.model_dump_json())

    assert serialized["referenced_tables"] == [
        "orders",
    ]
    assert serialized["referenced_columns"] == []
    assert serialized["validation_status"] == "validated"


@pytest.mark.parametrize(
    "prompt",
    [
        SQL_PROPOSAL_SYSTEM_MESSAGE,
        SQL_REPAIR_SYSTEM_MESSAGE,
    ],
)
def test_prompts_document_controlled_cte_and_count(
    prompt: str,
) -> None:
    normalized = prompt.casefold()

    assert "at most two independent" in normalized
    assert "non-recursive" in normalized
    assert "top-level ctes" in normalized
    assert "explicit named columns" in normalized
    assert "count(*) only for row counts" in normalized
    assert "never use qualified stars" in normalized
