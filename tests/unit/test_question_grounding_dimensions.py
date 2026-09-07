"""Regression: compound dimension phrases must ground to a single dimension.

Covers the fix that adds the compound synonym "categoria de produto" to the
"category" dimension and applies _remove_contained_candidates to dimension
candidates, so a contained match (e.g. "product") is dropped when a longer
compound match (e.g. "category") covers the same span.
"""

from backend.app.services.question_grounding import ground_question


def test_category_product_grounds_to_single_dimension() -> None:
    result = ground_question(
        "Qual o total de vendas por categoria de produto?"
    )
    assert result.status == "grounded"
    assert result.dimensions == ("category",)

def test_sales_channel_remains_single_dimension() -> None:
    result = ground_question(
        "Qual a receita por canal de venda?"
    )
    assert result.status == "grounded"
    assert result.dimensions == ("sales_channel",)
