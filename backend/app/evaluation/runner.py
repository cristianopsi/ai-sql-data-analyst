from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.evaluation.contracts import (
    ALL_EVALUATION_METRICS,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationMetricName,
    EvaluationMetricObservation,
    EvaluationMetricResult,
    EvaluationReport,
    calculate_evaluation_rate,
)


class EvaluationRunnerError(RuntimeError):
    """Sanitized failure raised by the systematic evaluation runner."""


@runtime_checkable
class EvaluationCaseExecutor(Protocol):
    """Explicit boundary for evaluating one reference case."""

    def execute(self, case: EvaluationCase) -> EvaluationCaseResult:
        """Execute one case and return only sanitized evaluation evidence."""
        ...


class EvaluationRunner:
    """Aggregate injected case evidence into a deterministic safe report."""

    def __init__(self, executor: EvaluationCaseExecutor) -> None:
        self._executor = executor

    def run(self, dataset: EvaluationDataset) -> EvaluationReport:
        case_results = tuple(self._execute_case(case) for case in dataset.cases)

        metrics = tuple(
            self._aggregate_metric(
                metric,
                case_results,
                dataset,
            )
            for metric in ALL_EVALUATION_METRICS
        )

        return EvaluationReport(
            dataset_version=dataset.dataset_version,
            total_cases=len(case_results),
            case_results=case_results,
            metrics=metrics,
            passed=all(metric.passed for metric in metrics),
        )

    def _execute_case(
        self,
        case: EvaluationCase,
    ) -> EvaluationCaseResult:
        try:
            result = self._executor.execute(case)
        except Exception as exc:
            raise EvaluationRunnerError(
                f"evaluation case execution failed for {case.case_id}"
            ) from exc

        if result.case_id != case.case_id:
            raise EvaluationRunnerError(
                "evaluation executor returned an incompatible case identifier"
            )

        if result.category is not case.category:
            raise EvaluationRunnerError("evaluation executor returned an incompatible category")

        expected_metrics = set(case.expectation.applicable_metrics)
        observed_by_metric = {
            observation.metric: observation for observation in result.observations
        }

        if set(observed_by_metric) != expected_metrics:
            raise EvaluationRunnerError("evaluation executor returned an incompatible metric set")

        classification_passed = result.actual_disposition is case.expectation.disposition

        grounding_observation = observed_by_metric.get(EvaluationMetricName.GROUNDING_ACCURACY)

        if (
            grounding_observation is not None
            and grounding_observation.passed
            and not classification_passed
        ):
            raise EvaluationRunnerError(
                "evaluation grounding classification evidence is inconsistent"
            )

        unsafe_block_observation = observed_by_metric.get(EvaluationMetricName.UNSAFE_BLOCK_RATE)

        if (
            unsafe_block_observation is not None
            and unsafe_block_observation.passed is not classification_passed
        ):
            raise EvaluationRunnerError("evaluation unsafe blocking evidence is inconsistent")

        canonical_observations = tuple(
            observed_by_metric[metric]
            for metric in ALL_EVALUATION_METRICS
            if metric in observed_by_metric
        )

        return EvaluationCaseResult(
            case_id=result.case_id,
            category=result.category,
            actual_disposition=result.actual_disposition,
            observations=canonical_observations,
        )

    @staticmethod
    def _aggregate_metric(
        metric: EvaluationMetricName,
        case_results: tuple[EvaluationCaseResult, ...],
        dataset: EvaluationDataset,
    ) -> EvaluationMetricResult:
        observations: list[EvaluationMetricObservation] = []

        for result in case_results:
            observations.extend(
                observation for observation in result.observations if observation.metric is metric
            )

        numerator = sum(observation.passed for observation in observations)
        denominator = len(observations)
        rate = calculate_evaluation_rate(numerator, denominator)
        threshold = dataset.thresholds.threshold_for(metric)

        return EvaluationMetricResult(
            metric=metric,
            numerator=numerator,
            denominator=denominator,
            rate=rate,
            threshold=threshold,
            passed=denominator > 0 and rate >= threshold,
        )


def create_evaluation_runner(
    executor: EvaluationCaseExecutor,
) -> EvaluationRunner:
    return EvaluationRunner(executor)
