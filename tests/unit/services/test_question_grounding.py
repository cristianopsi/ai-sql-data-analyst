import pytest
from pydantic import ValidationError

from backend.app.schemas.grounding import (
    QuestionGrounding,
)
from backend.app.services.question_grounding import (
    QuestionGroundingError,
    ground_question,
)


def test_revenue_by_region_and_year_is_grounded() -> None:
    result = ground_question("Qual foi o faturamento por região em 2025?")

    assert result.status == "grounded"
    assert result.metrics == ("approved_revenue",)
    assert result.dimensions == (
        "order_date",
        "region",
    )
    assert result.tables == (
        "orders",
        "payments",
        "regions",
    )
    assert result.relationships == (
        "orders_region_id_to_regions_id",
        "payments_order_id_to_orders_id",
    )
    assert {
        "approved_revenue_only",
        "sales_region_at_order_time",
        "brl_currency",
    } <= set(result.business_rules)


def test_target_phrase_suppresses_generic_revenue_match() -> None:
    result = ground_question("Mostre a meta de receita por região e mês")

    assert result.status == "grounded"
    assert result.metrics == ("revenue_target",)
    assert result.dimensions == (
        "region",
        "target_month",
    )
    assert result.tables == (
        "regions",
        "sales_targets",
    )
    assert result.relationships == ("sales_targets_region_id_to_regions_id",)


def test_order_value_and_channel_are_grounded() -> None:
    result = ground_question("Quantidade de pedidos cancelados por canal")

    assert result.status == "grounded"
    assert result.metrics == ("order_count",)
    assert {
        "order_status",
        "sales_channel",
    } == set(result.dimensions)
    assert result.tables == ("orders",)
    assert result.relationships == ()
    assert len(result.values) == 1
    assert result.values[0].dimension_name == "order_status"
    assert result.values[0].value == "cancelled"


def test_average_ticket_by_payment_method_is_grounded() -> None:
    result = ground_question("Ticket médio por forma de pagamento")

    assert result.status == "grounded"
    assert result.metrics == ("average_approved_order_value",)
    assert result.dimensions == ("payment_method",)
    assert result.tables == ("payments",)
    assert {
        "approved_revenue_only",
        "brl_currency",
    } <= set(result.business_rules)


def test_restricted_customer_intent_is_blocked() -> None:
    result = ground_question("Liste os emails dos clientes")

    assert result.status == "restricted"
    assert result.metrics == ()
    assert result.dimensions == ()
    assert result.tables == ()
    assert result.relationships == ()
    assert result.matches == ()


def test_unsupported_question_has_no_schema_context() -> None:
    result = ground_question("Qual é a temperatura hoje?")

    assert result.status == "unsupported"
    assert result.metrics == ()
    assert result.dimensions == ()
    assert result.tables == ()
    assert result.relationships == ()


@pytest.mark.parametrize(
    ("question", "maximum", "message"),
    [
        (
            "   ",
            2_000,
            "cannot be empty",
        ),
        (
            "pergunta muito longa",
            5,
            "exceeds maximum length",
        ),
    ],
)
def test_invalid_question_is_rejected(
    question: str,
    maximum: int,
    message: str,
) -> None:
    with pytest.raises(
        QuestionGroundingError,
        match=message,
    ):
        ground_question(
            question,
            max_question_length=maximum,
        )


def test_grounding_is_deterministic_and_immutable() -> None:
    question = "Faturamento por categoria em 2024"

    first = ground_question(question)
    second = ground_question(question)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        first.status = "unsupported"


def test_grounding_result_uses_typed_contract() -> None:
    result = ground_question("Pedidos por região")

    assert isinstance(
        result,
        QuestionGrounding,
    )
    assert result.semantic_version == "1"
    assert result.status == "grounded"
