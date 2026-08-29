from backend.app.evaluation import (
    EvaluationCaseCategory,
    load_reference_evaluation_dataset,
)
from backend.app.services.question_grounding import ground_question
from backend.app.services.semantic_layer import build_semantic_layer

EXPECTED_STATUS = {
    EvaluationCaseCategory.VALID: "grounded",
    EvaluationCaseCategory.AMBIGUOUS: "ambiguous",
    EvaluationCaseCategory.RESTRICTED: "restricted",
    EvaluationCaseCategory.OUT_OF_DOMAIN: "unsupported",
}


def test_reference_questions_map_to_expected_grounding_statuses() -> None:
    dataset = load_reference_evaluation_dataset()

    for case in dataset.cases:
        grounding = ground_question(case.question)

        assert grounding.status == EXPECTED_STATUS[case.category]


def test_valid_semantic_criteria_match_real_semantic_layer() -> None:
    dataset = load_reference_evaluation_dataset()
    semantic_layer = build_semantic_layer()

    metrics = {metric.name: metric for metric in semantic_layer.metrics}

    for case in dataset.cases:
        if case.category is not EvaluationCaseCategory.VALID:
            continue

        grounding = ground_question(case.question)
        grounded_metrics = set(grounding.metrics)
        grounded_dimensions = set(grounding.dimensions)

        for criterion in case.expectation.semantic_criteria:
            criterion_type, separator, expected_value = criterion.partition(":")

            assert separator == ":"

            if criterion_type == "metric":
                assert expected_value in grounded_metrics
            elif criterion_type == "dimension":
                assert expected_value in grounded_dimensions
            elif criterion_type == "aggregation":
                assert any(
                    metrics[metric_name].aggregation == expected_value
                    for metric_name in grounded_metrics
                )
            else:
                raise AssertionError(f"unsupported valid criterion type: {criterion_type}")


def test_restricted_cases_expose_no_grounded_identifiers() -> None:
    dataset = load_reference_evaluation_dataset()

    restricted_cases = (
        case for case in dataset.cases if case.category is EvaluationCaseCategory.RESTRICTED
    )

    for case in restricted_cases:
        grounding = ground_question(case.question)

        assert grounding.status == "restricted"
        assert grounding.metrics == ()
        assert grounding.dimensions == ()
        assert grounding.values == ()
        assert grounding.tables == ()
        assert grounding.matches == ()


def test_unsupported_cases_expose_no_grounded_identifiers() -> None:
    dataset = load_reference_evaluation_dataset()

    unsupported_cases = (
        case for case in dataset.cases if case.category is EvaluationCaseCategory.OUT_OF_DOMAIN
    )

    for case in unsupported_cases:
        grounding = ground_question(case.question)

        assert grounding.status == "unsupported"
        assert grounding.metrics == ()
        assert grounding.dimensions == ()
        assert grounding.values == ()
        assert grounding.tables == ()
        assert grounding.matches == ()
