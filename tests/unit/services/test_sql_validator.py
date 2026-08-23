import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMTokenUsage,
)
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.schemas.sql_generation import (
    SQLProposal,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)
from backend.app.services.grounding_context import (
    build_grounding_context,
)
from backend.app.services.sql_validator import (
    SQLValidationError,
    SQLValidator,
    create_sql_validator,
    validate_sql_proposal,
)

SAFE_SQL = """
SELECT
    DATE_TRUNC('month', o.placed_at) AS month,
    r.name AS region,
    COUNT(DISTINCT o.id) AS order_count,
    SUM(p.amount) AS approved_revenue,
    ROUND(AVG(p.amount), 2) AS average_value
FROM retail.payments AS p
JOIN retail.orders AS o
    ON o.id = p.order_id
JOIN retail.regions AS r
    ON r.id = o.region_id
WHERE p.status = 'approved'
GROUP BY
    DATE_TRUNC('month', o.placed_at),
    r.name
ORDER BY approved_revenue DESC
""".strip()


def grounded_context() -> CompactGroundingContext:
    return build_grounding_context("Qual foi o faturamento por região em 2025?")


def proposal_for(
    sql: str,
    context: CompactGroundingContext | None = None,
) -> SQLProposal:
    active_context = context if context is not None else grounded_context()

    return SQLProposal(
        context_version=(active_context.context_version),
        semantic_version=(active_context.semantic_version),
        catalog_version=(active_context.catalog_version),
        provider="mock",
        model="deterministic-test",
        sql=sql,
        explanation=("Controlled test SQL proposal."),
        usage=LLMTokenUsage(
            input_tokens=10,
            output_tokens=20,
        ),
    )


def test_safe_select_is_validated_and_limited() -> None:
    context = grounded_context()

    result = validate_sql_proposal(
        proposal_for(
            SAFE_SQL,
            context,
        ),
        context,
        max_result_rows=100,
    )

    assert result.validation_status == ("validated")
    assert result.row_limit == 100
    assert result.sql.endswith("LIMIT 100")
    assert result.referenced_tables == (
        "orders",
        "payments",
        "regions",
    )
    assert "orders.placed_at" in (result.referenced_columns)
    assert "payments.amount" in (result.referenced_columns)
    assert "regions.name" in (result.referenced_columns)


def test_existing_limit_is_preserved_or_clamped() -> None:
    context = grounded_context()

    preserved = validate_sql_proposal(
        proposal_for(
            ("SELECT o.id FROM retail.orders AS o LIMIT 25"),
            context,
        ),
        context,
        max_result_rows=100,
    )

    clamped = validate_sql_proposal(
        proposal_for(
            ("SELECT o.id FROM retail.orders AS o LIMIT 5000"),
            context,
        ),
        context,
        max_result_rows=100,
    )

    assert preserved.row_limit == 25
    assert preserved.sql.endswith("LIMIT 25")
    assert clamped.row_limit == 100
    assert clamped.sql.endswith("LIMIT 100")


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "SELECT 1; SELECT 2",
        "DELETE FROM retail.orders",
        ("UPDATE retail.orders SET status = 'cancelled'"),
        ("INSERT INTO retail.orders (id) VALUES (1)"),
        "DROP TABLE retail.orders",
        ("ALTER TABLE retail.orders ADD COLUMN unsafe TEXT"),
        "COPY retail.orders TO STDOUT",
        "SELECT * FROM retail.orders",
        ("SELECT o.* FROM retail.orders AS o"),
        "SELECT pg_sleep(1)",
        ("SELECT relname FROM pg_catalog.pg_class"),
        ("SELECT table_name FROM information_schema.tables"),
        ("SELECT o.id FROM retail.orders AS o FOR UPDATE"),
        ("SELECT o.id INTO TEMPORARY TABLE copied_orders FROM retail.orders AS o"),
        ("SELECT id FROM retail.orders UNION ALL SELECT id FROM retail.payments"),
        ("WITH approved AS (SELECT order_id FROM retail.payments) SELECT order_id FROM approved"),
        ("SELECT o.id FROM retail.orders AS o -- uncontrolled comment"),
        ("SELECT secret_value FROM retail.unknown_table"),
        ("SELECT o.email FROM retail.orders AS o"),
        ("SELECT status FROM retail.orders AS o JOIN retail.payments AS p ON p.order_id = o.id"),
        ("SELECT o.id FROM analytics.retail.orders AS o"),
        "SELECT 1",
        ("SELECT nested.id FROM (SELECT o.id FROM retail.orders AS o) AS nested"),
        ("SELECT nextval('unsafe_sequence') FROM retail.orders AS o"),
    ],
)
def test_unsafe_sql_is_rejected(
    unsafe_sql: str,
) -> None:
    context = grounded_context()

    with pytest.raises(
        SQLValidationError,
        match="failed security validation",
    ) as captured:
        validate_sql_proposal(
            proposal_for(
                unsafe_sql,
                context,
            ),
            context,
            max_result_rows=100,
        )

    assert unsafe_sql not in str(captured.value)


def test_context_versions_must_match() -> None:
    context = grounded_context()
    proposal = proposal_for(
        ("SELECT o.id FROM retail.orders AS o"),
        context,
    )

    mismatched = proposal.model_copy(
        update={
            "semantic_version": "different",
        }
    )

    with pytest.raises(
        SQLValidationError,
        match="failed security validation",
    ):
        validate_sql_proposal(
            mismatched,
            context,
            max_result_rows=100,
        )


def test_non_grounded_context_is_rejected() -> None:
    context = build_grounding_context("Qual é a temperatura hoje?")
    proposal = proposal_for(
        ("SELECT o.id FROM retail.orders AS o"),
        context,
    )

    with pytest.raises(
        SQLValidationError,
        match="failed security validation",
    ):
        validate_sql_proposal(
            proposal,
            context,
            max_result_rows=100,
        )


def test_invalid_row_limit_is_rejected() -> None:
    context = grounded_context()

    with pytest.raises(
        ValueError,
        match="must be positive",
    ):
        validate_sql_proposal(
            proposal_for(
                SAFE_SQL,
                context,
            ),
            context,
            max_result_rows=0,
        )


def test_validator_factory_uses_settings() -> None:
    validator = create_sql_validator(
        Settings(
            max_result_rows=321,
        )
    )

    assert isinstance(
        validator,
        SQLValidator,
    )
    assert validator.max_result_rows == 321

    context = grounded_context()
    result = validator.validate(
        proposal_for(
            SAFE_SQL,
            context,
        ),
        context,
    )

    assert result.row_limit == 321


def test_validated_sql_is_immutable() -> None:
    context = grounded_context()
    result = validate_sql_proposal(
        proposal_for(
            SAFE_SQL,
            context,
        ),
        context,
        max_result_rows=100,
    )

    assert isinstance(
        result,
        ValidatedSQL,
    )

    with pytest.raises(ValidationError):
        result.sql = "DELETE"  # type: ignore[misc]
