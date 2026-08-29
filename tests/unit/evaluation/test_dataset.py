from decimal import Decimal
from importlib.resources import files

import pytest
from pydantic import BaseModel, ValidationError

from backend.app.evaluation import (
    ALL_EVALUATION_METRICS,
    EvaluationCaseCategory,
    EvaluationDataset,
    EvaluationDatasetError,
    EvaluationDisposition,
    EvaluationExpectation,
    EvaluationMetricName,
    EvaluationMetricResult,
    EvaluationThresholds,
    calculate_evaluation_rate,
    load_reference_evaluation_dataset,
    parse_evaluation_dataset,
)


def _attempt_frozen_mutation(
    model: BaseModel,
    field_name: str,
    value: object,
) -> None:
    setattr(model, field_name, value)


def test_reference_dataset_loads_all_categories_and_metrics() -> None:
    dataset = load_reference_evaluation_dataset()

    assert dataset.contract_version == "1"
    assert dataset.dataset_version == "1.1.0"
    assert len(dataset.cases) == 10
    assert {case.category for case in dataset.cases} == set(EvaluationCaseCategory)
    assert {
        metric for case in dataset.cases for metric in case.expectation.applicable_metrics
    } == set(EvaluationMetricName)


def test_reference_dataset_identifiers_are_unique() -> None:
    dataset = load_reference_evaluation_dataset()
    case_ids = tuple(case.case_id for case in dataset.cases)

    assert len(case_ids) == len(set(case_ids))


def test_category_dispositions_are_exact() -> None:
    dataset = load_reference_evaluation_dataset()

    expected = {
        EvaluationCaseCategory.VALID: EvaluationDisposition.ALLOW,
        EvaluationCaseCategory.AMBIGUOUS: EvaluationDisposition.CLARIFY,
        EvaluationCaseCategory.RESTRICTED: EvaluationDisposition.BLOCK,
        EvaluationCaseCategory.OUT_OF_DOMAIN: EvaluationDisposition.BLOCK,
    }

    assert all(case.expectation.disposition is expected[case.category] for case in dataset.cases)


def test_repair_metric_has_an_applicable_reference_case() -> None:
    dataset = load_reference_evaluation_dataset()

    repair_cases = [case for case in dataset.cases if case.expectation.repair_expected]

    assert len(repair_cases) == 1
    assert (
        EvaluationMetricName.REPAIR_SUCCESS_RATE in repair_cases[0].expectation.applicable_metrics
    )


def test_dataset_contract_is_immutable() -> None:
    dataset = load_reference_evaluation_dataset()

    with pytest.raises(ValidationError, match="frozen"):
        _attempt_frozen_mutation(dataset, "dataset_version", "2.0.0")

    with pytest.raises(ValidationError, match="frozen"):
        _attempt_frozen_mutation(dataset.cases[0], "question", "changed")


def test_allowed_expectation_requires_one_evaluation_target() -> None:
    with pytest.raises(
        ValidationError,
        match="exactly one of expected_sql or semantic_criteria",
    ):
        EvaluationExpectation(
            disposition=EvaluationDisposition.ALLOW,
            expected_sql="SELECT 1",
            semantic_criteria=("metric:revenue",),
            applicable_metrics=(EvaluationMetricName.GROUNDING_ACCURACY,),
        )


def test_blocked_expectation_rejects_expected_sql() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot contain expected SQL",
    ):
        EvaluationExpectation(
            disposition=EvaluationDisposition.BLOCK,
            expected_sql="SELECT 1",
            semantic_criteria=(),
            applicable_metrics=(EvaluationMetricName.UNSAFE_BLOCK_RATE,),
        )


def test_dataset_rejects_duplicate_case_identifiers() -> None:
    dataset = load_reference_evaluation_dataset()
    raw_dataset = dataset.model_dump(mode="python")
    raw_dataset["cases"] = (
        *raw_dataset["cases"],
        raw_dataset["cases"][0],
    )

    with pytest.raises(
        ValidationError,
        match="identifiers must be unique",
    ):
        EvaluationDataset.model_validate(raw_dataset)


def test_dataset_rejects_missing_category() -> None:
    dataset = load_reference_evaluation_dataset()
    raw_dataset = dataset.model_dump(mode="python")
    raw_dataset["cases"] = [
        case
        for case in raw_dataset["cases"]
        if case["category"] is not EvaluationCaseCategory.OUT_OF_DOMAIN
    ]

    with pytest.raises(
        ValidationError,
        match="represent all case categories",
    ):
        EvaluationDataset.model_validate(raw_dataset)


def test_dataset_parser_rejects_sensitive_keys() -> None:
    content = """
contract_version: "1"
dataset_version: "1.0.0"
api_key: placeholder
thresholds: {}
cases: []
"""

    with pytest.raises(
        EvaluationDatasetError,
        match="sensitive key",
    ):
        parse_evaluation_dataset(content)


def test_rate_calculation_is_deterministic_and_fail_closed() -> None:
    assert calculate_evaluation_rate(2, 3) == Decimal("0.6667")
    assert calculate_evaluation_rate(0, 0) == Decimal("0.0000")

    result = EvaluationMetricResult(
        metric=EvaluationMetricName.REPAIR_SUCCESS_RATE,
        numerator=0,
        denominator=0,
        rate=Decimal("0.0000"),
        threshold=Decimal("0.8000"),
        passed=False,
    )

    assert result.passed is False


def test_thresholds_cover_every_official_metric() -> None:
    thresholds = EvaluationThresholds()

    assert tuple(thresholds.threshold_for(metric) for metric in ALL_EVALUATION_METRICS) == (
        Decimal("0.9000"),
        Decimal("0.9000"),
        Decimal("0.8000"),
        Decimal("1.0000"),
        Decimal("1.0000"),
        Decimal("1.0000"),
    )


def test_report_contract_has_no_sensitive_payload_fields() -> None:
    from backend.app.evaluation import EvaluationCaseResult, EvaluationReport

    assert "question" not in EvaluationCaseResult.model_fields
    assert "sql" not in EvaluationCaseResult.model_fields
    assert "rows" not in EvaluationCaseResult.model_fields
    assert "credentials" not in EvaluationCaseResult.model_fields

    assert "question" not in EvaluationReport.model_fields
    assert "sql" not in EvaluationReport.model_fields
    assert "rows" not in EvaluationReport.model_fields
    assert "credentials" not in EvaluationReport.model_fields


def test_reference_dataset_resource_is_readable() -> None:
    resource = files("backend.app.evaluation.data").joinpath("reference_questions.yaml")

    assert resource.is_file()
    assert resource.read_text(encoding="utf-8").startswith('contract_version: "1"')
