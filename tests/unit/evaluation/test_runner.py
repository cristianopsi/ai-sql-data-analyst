from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from backend.app.evaluation import (
    ALL_EVALUATION_METRICS,
    EvaluationCase,
    EvaluationCaseCategory,
    EvaluationCaseExecutor,
    EvaluationCaseResult,
    EvaluationMetricName,
    EvaluationMetricObservation,
    EvaluationRunner,
    EvaluationRunnerError,
    create_evaluation_runner,
    load_reference_evaluation_dataset,
)

ResultTransform = Callable[
    [EvaluationCase, EvaluationCaseResult],
    EvaluationCaseResult,
]


def _passing_result(case: EvaluationCase) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        case_id=case.case_id,
        category=case.category,
        actual_disposition=case.expectation.disposition,
        observations=tuple(
            EvaluationMetricObservation(
                metric=metric,
                passed=True,
            )
            for metric in case.expectation.applicable_metrics
        ),
    )


class RecordingExecutor:
    def __init__(
        self,
        transform: ResultTransform | None = None,
    ) -> None:
        self.case_ids: list[str] = []
        self._transform = transform

    def execute(self, case: EvaluationCase) -> EvaluationCaseResult:
        self.case_ids.append(case.case_id)
        result = _passing_result(case)

        if self._transform is None:
            return result

        return self._transform(case, result)


class FailingExecutor:
    def execute(self, case: EvaluationCase) -> EvaluationCaseResult:
        raise RuntimeError(case.question)


def test_runner_generates_successful_complete_report() -> None:
    dataset = load_reference_evaluation_dataset()
    report = EvaluationRunner(RecordingExecutor()).run(dataset)

    assert report.dataset_version == dataset.dataset_version
    assert report.total_cases == len(dataset.cases)
    assert report.passed is True
    assert tuple(metric.metric for metric in report.metrics) == (ALL_EVALUATION_METRICS)
    assert all(metric.passed for metric in report.metrics)


def test_runner_output_is_deterministic() -> None:
    dataset = load_reference_evaluation_dataset()
    runner = EvaluationRunner(RecordingExecutor())

    first = runner.run(dataset)
    second = runner.run(dataset)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_runner_uses_dataset_thresholds() -> None:
    dataset = load_reference_evaluation_dataset()
    report = EvaluationRunner(RecordingExecutor()).run(dataset)

    thresholds = {metric.metric: metric.threshold for metric in report.metrics}

    assert thresholds == {
        EvaluationMetricName.GROUNDING_ACCURACY: Decimal("0.9000"),
        EvaluationMetricName.SQL_VALIDATION_RATE: Decimal("0.9000"),
        EvaluationMetricName.REPAIR_SUCCESS_RATE: Decimal("0.8000"),
        EvaluationMetricName.UNSAFE_BLOCK_RATE: Decimal("1.0000"),
        EvaluationMetricName.CALCULATION_CONSISTENCY: Decimal("1.0000"),
        EvaluationMetricName.INSIGHT_FIDELITY: Decimal("1.0000"),
    }


def test_failed_metric_fails_report() -> None:
    def fail_one_insight(
        case: EvaluationCase,
        result: EvaluationCaseResult,
    ) -> EvaluationCaseResult:
        if case.case_id != "valid_revenue_by_region":
            return result

        observations = tuple(
            observation.model_copy(update={"passed": False})
            if observation.metric is EvaluationMetricName.INSIGHT_FIDELITY
            else observation
            for observation in result.observations
        )

        return result.model_copy(update={"observations": observations})

    dataset = load_reference_evaluation_dataset()
    report = EvaluationRunner(RecordingExecutor(fail_one_insight)).run(dataset)

    insight = next(
        metric
        for metric in report.metrics
        if metric.metric is EvaluationMetricName.INSIGHT_FIDELITY
    )

    assert insight.numerator == 3
    assert insight.denominator == 4
    assert insight.rate == Decimal("0.7500")
    assert insight.passed is False
    assert report.passed is False


def test_runner_executes_every_case_once_in_dataset_order() -> None:
    dataset = load_reference_evaluation_dataset()
    executor = RecordingExecutor()

    EvaluationRunner(executor).run(dataset)

    assert executor.case_ids == [case.case_id for case in dataset.cases]


def test_runner_rejects_incompatible_case_identifier() -> None:
    def change_identifier(
        case: EvaluationCase,
        result: EvaluationCaseResult,
    ) -> EvaluationCaseResult:
        return result.model_copy(update={"case_id": "different_case"})

    dataset = load_reference_evaluation_dataset()

    with pytest.raises(
        EvaluationRunnerError,
        match="incompatible case identifier",
    ):
        EvaluationRunner(RecordingExecutor(change_identifier)).run(dataset)


def test_runner_rejects_incompatible_category() -> None:
    def change_category(
        case: EvaluationCase,
        result: EvaluationCaseResult,
    ) -> EvaluationCaseResult:
        return result.model_copy(update={"category": EvaluationCaseCategory.RESTRICTED})

    dataset = load_reference_evaluation_dataset()

    with pytest.raises(
        EvaluationRunnerError,
        match="incompatible category",
    ):
        EvaluationRunner(RecordingExecutor(change_category)).run(dataset)


def test_runner_rejects_missing_metric_observation() -> None:
    def remove_observation(
        case: EvaluationCase,
        result: EvaluationCaseResult,
    ) -> EvaluationCaseResult:
        return result.model_copy(update={"observations": result.observations[:-1]})

    dataset = load_reference_evaluation_dataset()

    with pytest.raises(
        EvaluationRunnerError,
        match="incompatible metric set",
    ):
        EvaluationRunner(RecordingExecutor(remove_observation)).run(dataset)


def test_runner_rejects_unexpected_metric_observation() -> None:
    def add_observation(
        case: EvaluationCase,
        result: EvaluationCaseResult,
    ) -> EvaluationCaseResult:
        if case.category is not EvaluationCaseCategory.AMBIGUOUS:
            return result

        return result.model_copy(
            update={
                "observations": (
                    *result.observations,
                    EvaluationMetricObservation(
                        metric=EvaluationMetricName.UNSAFE_BLOCK_RATE,
                        passed=True,
                    ),
                )
            }
        )

    dataset = load_reference_evaluation_dataset()

    with pytest.raises(
        EvaluationRunnerError,
        match="incompatible metric set",
    ):
        EvaluationRunner(RecordingExecutor(add_observation)).run(dataset)


def test_runner_rejects_inconsistent_classification_evidence() -> None:
    def change_disposition(
        case: EvaluationCase,
        result: EvaluationCaseResult,
    ) -> EvaluationCaseResult:
        if case.category is not EvaluationCaseCategory.VALID:
            return result

        return result.model_copy(update={"actual_disposition": "block"})

    dataset = load_reference_evaluation_dataset()

    with pytest.raises(
        EvaluationRunnerError,
        match="classification evidence is inconsistent",
    ):
        EvaluationRunner(RecordingExecutor(change_disposition)).run(dataset)


def test_executor_failure_is_sanitized() -> None:
    dataset = load_reference_evaluation_dataset()
    first_question = dataset.cases[0].question

    with pytest.raises(
        EvaluationRunnerError,
        match="evaluation case execution failed",
    ) as captured:
        EvaluationRunner(FailingExecutor()).run(dataset)

    assert first_question not in str(captured.value)
    assert "SELECT" not in str(captured.value)
    assert "rows" not in str(captured.value)


def test_factory_returns_runner_with_protocol_executor() -> None:
    executor = RecordingExecutor()
    runner = create_evaluation_runner(executor)

    assert isinstance(executor, EvaluationCaseExecutor)
    assert isinstance(runner, EvaluationRunner)
