from __future__ import annotations

from typing import Protocol

from backend.app.evaluation.contracts import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDisposition,
    EvaluationMetricName,
    EvaluationMetricObservation,
)
from backend.app.schemas.analytics import DeterministicAnalyticsResult
from backend.app.schemas.grounding import QuestionGrounding
from backend.app.schemas.insights import GroundedInsightResult
from backend.app.schemas.query_execution import QueryExecutionResult
from backend.app.schemas.semantic_context import CompactGroundingContext
from backend.app.schemas.sql_generation import SQLGenerationResult
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
)
from backend.app.services.question_grounding import ground_question


class PipelineEvaluationAdapterError(RuntimeError):
    """Sanitized adapter failure without questions, SQL, rows, or secrets."""


class GroundingCallable(Protocol):
    def __call__(self, question: str) -> QuestionGrounding: ...


class SQLGenerationComponent(Protocol):
    def generate(self, question: str) -> SQLGenerationResult: ...


class QueryExecutionComponent(Protocol):
    def execute(
        self,
        generation: SQLGenerationResult,
    ) -> QueryExecutionResult: ...


class AnalyticsComponent(Protocol):
    def analyze(
        self,
        execution: QueryExecutionResult,
        context: CompactGroundingContext,
    ) -> DeterministicAnalyticsResult: ...


class VisualizationComponent(Protocol):
    def specify(
        self,
        analytics: DeterministicAnalyticsResult,
    ) -> DeterministicVisualizationResult: ...


class InsightComponent(Protocol):
    def generate(
        self,
        analytics: DeterministicAnalyticsResult,
        visualizations: DeterministicVisualizationResult,
    ) -> GroundedInsightResult: ...


_GROUNDING_DISPOSITIONS = {
    "grounded": EvaluationDisposition.ALLOW,
    "ambiguous": EvaluationDisposition.CLARIFY,
    "restricted": EvaluationDisposition.BLOCK,
    "unsupported": EvaluationDisposition.BLOCK,
}


class RealPipelineEvaluationAdapter:
    """Evaluate the real component chain while returning only safe evidence."""

    def __init__(
        self,
        *,
        pipeline: SQLGenerationComponent,
        executor: QueryExecutionComponent,
        analytics_engine: AnalyticsComponent,
        visualization_engine: VisualizationComponent,
        insight_engine: InsightComponent,
        grounder: GroundingCallable = ground_question,
    ) -> None:
        self._pipeline = pipeline
        self._executor = executor
        self._analytics_engine = analytics_engine
        self._visualization_engine = visualization_engine
        self._insight_engine = insight_engine
        self._grounder = grounder

    def execute(self, case: EvaluationCase) -> EvaluationCaseResult:
        grounding = self._ground(case)
        actual_disposition = self._disposition(grounding)

        observations = {metric: False for metric in case.expectation.applicable_metrics}

        if EvaluationMetricName.GROUNDING_ACCURACY in observations:
            observations[EvaluationMetricName.GROUNDING_ACCURACY] = (
                actual_disposition is case.expectation.disposition
                and self._criteria_match(
                    case,
                    grounding,
                    actual_disposition,
                    context=None,
                )
            )

        if EvaluationMetricName.UNSAFE_BLOCK_RATE in observations:
            observations[EvaluationMetricName.UNSAFE_BLOCK_RATE] = (
                actual_disposition is EvaluationDisposition.BLOCK
                and actual_disposition is case.expectation.disposition
            )

        if (
            case.expectation.disposition is not EvaluationDisposition.ALLOW
            or actual_disposition is not EvaluationDisposition.ALLOW
        ):
            return self._result(
                case,
                actual_disposition,
                observations,
            )

        try:
            generation = self._pipeline.generate(case.question)
            context = generation.internal_context

            if context is None:
                return self._result(
                    case,
                    actual_disposition,
                    observations,
                )

            if EvaluationMetricName.GROUNDING_ACCURACY in observations:
                observations[EvaluationMetricName.GROUNDING_ACCURACY] = self._criteria_match(
                    case,
                    grounding,
                    actual_disposition,
                    context=context,
                )

            if EvaluationMetricName.SQL_VALIDATION_RATE in observations:
                observations[EvaluationMetricName.SQL_VALIDATION_RATE] = self._sql_is_valid(
                    case, generation, context
                )

            if EvaluationMetricName.REPAIR_SUCCESS_RATE in observations:
                observations[EvaluationMetricName.REPAIR_SUCCESS_RATE] = (
                    generation.repair_attempts > 0
                    and generation.generation_attempts > generation.repair_attempts
                )

            execution = self._executor.execute(generation)
            analytics = self._analytics_engine.analyze(
                execution,
                context,
            )
            visualizations = self._visualization_engine.specify(analytics)

            if EvaluationMetricName.CALCULATION_CONSISTENCY in observations:
                observations[EvaluationMetricName.CALCULATION_CONSISTENCY] = (
                    self._calculation_is_consistent(
                        execution,
                        analytics,
                        visualizations,
                    )
                )

            insights = self._insight_engine.generate(
                analytics,
                visualizations,
            )

            if EvaluationMetricName.INSIGHT_FIDELITY in observations:
                observations[EvaluationMetricName.INSIGHT_FIDELITY] = self._insight_is_faithful(
                    analytics,
                    visualizations,
                    insights,
                )
        except (RuntimeError, ValueError):
            return self._result(
                case,
                actual_disposition,
                observations,
            )

        return self._result(
            case,
            actual_disposition,
            observations,
        )

    def _ground(self, case: EvaluationCase) -> QuestionGrounding:
        try:
            return self._grounder(case.question)
        except (RuntimeError, ValueError) as exc:
            raise PipelineEvaluationAdapterError(
                f"evaluation grounding failed for {case.case_id}"
            ) from exc

    @staticmethod
    def _disposition(
        grounding: QuestionGrounding,
    ) -> EvaluationDisposition:
        disposition = _GROUNDING_DISPOSITIONS.get(grounding.status)

        if disposition is None:
            raise PipelineEvaluationAdapterError(
                "evaluation grounding returned an unsupported status"
            )

        return disposition

    @staticmethod
    def _criteria_match(
        case: EvaluationCase,
        grounding: QuestionGrounding,
        disposition: EvaluationDisposition,
        *,
        context: CompactGroundingContext | None,
    ) -> bool:
        if case.expectation.expected_sql is not None:
            return disposition is EvaluationDisposition.ALLOW

        grounded_metrics = set(grounding.metrics)
        grounded_dimensions = set(grounding.dimensions)

        context_metrics = (
            {metric.name: metric for metric in context.metrics} if context is not None else {}
        )

        for criterion in case.expectation.semantic_criteria:
            criterion_type, separator, expected_value = criterion.partition(":")

            if separator != ":":
                return False

            if criterion_type == "metric":
                matches = expected_value in grounded_metrics
            elif criterion_type in {"dimension", "time_dimension"}:
                matches = expected_value in grounded_dimensions
            elif criterion_type == "aggregation":
                matches = any(
                    context_metrics[metric_name].aggregation == expected_value
                    for metric_name in grounded_metrics
                    if metric_name in context_metrics
                )
            elif criterion_type == "clarification":
                matches = disposition is EvaluationDisposition.CLARIFY
            elif criterion_type in {"policy", "domain"}:
                matches = disposition is EvaluationDisposition.BLOCK
            else:
                matches = False

            if not matches:
                return False

        return bool(case.expectation.semantic_criteria)

    @staticmethod
    def _sql_is_valid(
        case: EvaluationCase,
        generation: SQLGenerationResult,
        context: CompactGroundingContext,
    ) -> bool:
        validated = generation.validated_sql

        if validated.validation_status != "validated":
            return False

        if context.grounding_status != "grounded":
            return False

        if generation.generation_attempts < 1:
            return False

        if generation.repair_attempts >= generation.generation_attempts:
            return False

        if not validated.referenced_tables:
            return False

        if case.expectation.expected_sql is not None:
            actual_sql = " ".join(validated.sql.split())
            expected_sql = " ".join(case.expectation.expected_sql.split())
            return actual_sql == expected_sql

        return True

    @staticmethod
    def _calculation_is_consistent(
        execution: QueryExecutionResult,
        analytics: DeterministicAnalyticsResult,
        visualizations: DeterministicVisualizationResult,
    ) -> bool:
        if execution.execution_status != "executed":
            return False

        if execution.transaction_read_only is not True:
            return False

        if analytics.analytics_status != "analyzed":
            return False

        if analytics.deterministic is not True:
            return False

        if analytics.calculation_scale != 4:
            return False

        if execution.row_count != analytics.source_row_count:
            return False

        if visualizations.source_row_count != analytics.source_row_count:
            return False

        if (
            visualizations.analytics_version != analytics.analytics_version
            or visualizations.execution_version != analytics.execution_version
            or visualizations.semantic_version != analytics.semantic_version
            or visualizations.catalog_version != analytics.catalog_version
        ):
            return False

        summaries = {summary.metric_name: summary for summary in analytics.metric_summaries}

        if not summaries or not visualizations.specifications:
            return False

        for summary in summaries.values():
            if summary.value_count < 1:
                return False
            if not (summary.minimum <= summary.average <= summary.maximum):
                return False

        ranking_keys = {
            (ranking.metric_name, ranking.dimension_name) for ranking in analytics.rankings
        }
        series_keys = {(series.metric_name, series.dimension_name) for series in analytics.series}

        for specification in visualizations.specifications:
            if specification.metric_name not in summaries:
                return False

            if specification.chart_type == "kpi":
                summary = summaries[specification.metric_name]

                if (
                    specification.value != summary.total
                    or specification.average != summary.average
                    or specification.minimum != summary.minimum
                    or specification.maximum != summary.maximum
                    or specification.value_count != summary.value_count
                ):
                    return False

            elif specification.chart_type in {"table", "bar"}:
                key = (
                    specification.metric_name,
                    specification.dimension_name,
                )

                if key not in ranking_keys:
                    return False

            elif specification.chart_type == "line":
                key = (
                    specification.metric_name,
                    specification.dimension_name,
                )

                if key not in series_keys:
                    return False
            else:
                return False

        return True

    @staticmethod
    def _insight_is_faithful(
        analytics: DeterministicAnalyticsResult,
        visualizations: DeterministicVisualizationResult,
        insights: GroundedInsightResult,
    ) -> bool:
        if insights.grounded is not True:
            return False

        if insights.calculated_by_llm is not False:
            return False

        if (
            insights.analytics_version != analytics.analytics_version
            or insights.visualization_version != visualizations.visualization_version
            or insights.execution_version != analytics.execution_version
            or insights.semantic_version != analytics.semantic_version
            or insights.catalog_version != analytics.catalog_version
            or insights.source_row_count != analytics.source_row_count
        ):
            return False

        if not insights.claims:
            return False

        metric_summary_names = {summary.metric_name for summary in analytics.metric_summaries}
        ranking_names = {ranking.metric_name for ranking in analytics.rankings}
        series_names = {series.metric_name for series in analytics.series}
        specification_ids = {
            specification.spec_id for specification in visualizations.specifications
        }

        claim_ids: set[str] = set()

        for claim in insights.claims:
            if claim.claim_id in claim_ids or not claim.evidence:
                return False

            claim_ids.add(claim.claim_id)

            for evidence in claim.evidence:
                if evidence.evidence_type == "metric_summary":
                    valid = evidence.metric_name in metric_summary_names
                elif evidence.evidence_type == "ranking":
                    valid = evidence.metric_name in ranking_names
                elif evidence.evidence_type == "series":
                    valid = evidence.metric_name in series_names
                elif evidence.evidence_type == "visualization":
                    valid = evidence.specification_id in specification_ids
                else:
                    valid = False

                if not valid:
                    return False

        return True

    @staticmethod
    def _result(
        case: EvaluationCase,
        disposition: EvaluationDisposition,
        observations: dict[EvaluationMetricName, bool],
    ) -> EvaluationCaseResult:
        return EvaluationCaseResult(
            case_id=case.case_id,
            category=case.category,
            actual_disposition=disposition,
            observations=tuple(
                EvaluationMetricObservation(
                    metric=metric,
                    passed=observations[metric],
                )
                for metric in case.expectation.applicable_metrics
            ),
        )


def create_real_pipeline_evaluation_adapter(
    *,
    pipeline: SQLGenerationComponent,
    executor: QueryExecutionComponent,
    analytics_engine: AnalyticsComponent,
    visualization_engine: VisualizationComponent,
    insight_engine: InsightComponent,
    grounder: GroundingCallable = ground_question,
) -> RealPipelineEvaluationAdapter:
    return RealPipelineEvaluationAdapter(
        pipeline=pipeline,
        executor=executor,
        analytics_engine=analytics_engine,
        visualization_engine=visualization_engine,
        insight_engine=insight_engine,
        grounder=grounder,
    )
