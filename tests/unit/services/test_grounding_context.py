import pytest
from pydantic import ValidationError

from backend.app.services.grounding_context import (
    GroundingContextError,
    build_grounding_context,
    serialize_grounding_context,
)
from backend.app.services.question_grounding import (
    QuestionGroundingError,
)


def test_revenue_context_contains_only_relevant_schema() -> None:
    context = build_grounding_context("Qual foi o faturamento por região em 2025?")

    assert context.context_version == "1"
    assert context.semantic_version == "1"
    assert context.catalog_version == "1"
    assert context.grounding_status == "grounded"

    assert tuple(metric.name for metric in context.metrics) == ("approved_revenue",)

    assert tuple(dimension.name for dimension in context.dimensions) == (
        "order_date",
        "region",
    )

    assert tuple(table.name for table in context.tables) == (
        "orders",
        "payments",
        "regions",
    )

    assert sum(len(table.columns) for table in context.tables) == 22

    assert tuple(relationship.name for relationship in (context.relationships)) == (
        "orders_region_id_to_regions_id",
        "payments_order_id_to_orders_id",
    )


def test_target_context_uses_monthly_regional_grain() -> None:
    context = build_grounding_context("Mostre a meta de receita por região e mês")

    assert tuple(metric.name for metric in context.metrics) == ("revenue_target",)

    assert tuple(dimension.name for dimension in context.dimensions) == (
        "region",
        "target_month",
    )

    assert tuple(table.name for table in context.tables) == (
        "regions",
        "sales_targets",
    )

    assert tuple(rule.name for rule in context.business_rules) == (
        "monthly_regional_targets",
        "brl_currency",
    )


def test_restricted_context_is_sanitized() -> None:
    context = build_grounding_context("Liste os emails dos clientes")
    serialized = serialize_grounding_context(context)

    assert context.grounding_status == "restricted"
    assert context.normalized_question is None
    assert context.metrics == ()
    assert context.dimensions == ()
    assert context.tables == ()
    assert context.relationships == ()
    assert "email" not in serialized
    assert "document_number" not in serialized


def test_unsupported_context_has_no_schema() -> None:
    context = build_grounding_context("Qual é a temperatura hoje?")

    assert context.grounding_status == "unsupported"
    assert context.normalized_question is None
    assert context.metrics == ()
    assert context.dimensions == ()
    assert context.tables == ()
    assert context.business_rules == ()


def test_context_serialization_is_safe_and_deterministic() -> None:
    question = "Faturamento por categoria em 2024"

    first = build_grounding_context(question)
    second = build_grounding_context(question)

    first_serialized = serialize_grounding_context(first)
    second_serialized = serialize_grounding_context(second)

    assert first == second
    assert first_serialized == second_serialized
    assert len(first_serialized) < 20_000
    assert "email" not in first_serialized
    assert "document_number" not in first_serialized


def test_context_size_limit_fails_closed() -> None:
    context = build_grounding_context("Pedidos por região")
    serialized = serialize_grounding_context(context)

    with pytest.raises(
        GroundingContextError,
        match="exceeds maximum size",
    ):
        serialize_grounding_context(
            context,
            max_characters=(len(serialized) - 1),
        )

    with pytest.raises(
        GroundingContextError,
        match="must be positive",
    ):
        serialize_grounding_context(
            context,
            max_characters=0,
        )


def test_question_length_limit_is_preserved() -> None:
    with pytest.raises(
        QuestionGroundingError,
        match="exceeds maximum length",
    ):
        build_grounding_context(
            "pergunta longa",
            max_question_length=5,
        )


def test_context_is_immutable() -> None:
    context = build_grounding_context("Pedidos por canal")

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        context.catalog_version = "changed"
