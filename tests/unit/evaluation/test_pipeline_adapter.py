from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.evaluation import (
    EvaluationCase,
    EvaluationCaseCategory,
    EvaluationMetricName,
    EvaluationRunner,
    PipelineEvaluationAdapterError,
    RealPipelineEvaluationAdapter,
    create_real_pipeline_evaluation_adapter,
    load_reference_evaluation_dataset,
)
from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.grounding import QuestionGrounding
from backend.app.schemas.insights import (
    GroundedInsightClaim,
    GroundedInsightResult,
    InsightEvidenceReference,
)
from backend.app.schemas.query_execution import QueryExecutionResult
from backend.app.schemas.sql_generation import SQLGenerationResult
from backend.app.schemas.sql_validation import ValidatedSQL
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
    KPIVisualizationSpec,
)
from backend.app.services.grounding_context import build_grounding_context


class StubPipeline:
    def __init__(
        self,
        *,
        repair_attempts: int = 1,
        context_available: bool = True,
        validation_status: str = "validated",
        fail: bool = False,
    ) -> None:
        self.calls = 0
        self.repair_attempts = repair_attempts
        self.context_available = context_available
        self.validation_status = validation_status
        self.fail = fail

    def generate(self, question: str) -> SQLGenerationResult:
        self.calls += 1

        if self.fail:
            raise RuntimeError(question)

        context = build_grounding_context(question) if self.context_available else None

        validated = ValidatedSQL.model_construct(
            validation_version="1",
            validation_status=self.validation_status,
            proposal_version="1",
            context_version="1",
            semantic_version="1",
            catalog_version="1",
            provider="mock",
            model="mock",
            sql="SELECT value FROM governed_table",
            explanation="controlled",
            row_limit=100,
            referenced_tables=("governed_table",),
            referenced_columns=("value",),
            usage=None,
        )

        return SQLGenerationResult.model_construct(
            generation_version="1",
            validated_sql=validated,
            internal_context=context,
            generation_attempts=self.repair_attempts + 1,
            repair_attempts=self.repair_attempts,
        )


class StubExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        generation: SQLGenerationResult,
    ) -> QueryExecutionResult:
        self.calls += 1

        return QueryExecutionResult.model_construct(
            execution_version="1",
            execution_status="executed",
            generation=generation,
            columns=("value",),
            internal_column_metadata=(),
            rows=({"value": Decimal("40")}, {"value": Decimal("60")}),
            row_count=2,
            statement_timeout_ms=1000,
            query_timeout_seconds=2.0,
            execution_time_ms=1.0,
            transaction_read_only=True,
        )


class StubAnalyticsEngine:
    def __init__(self, *, source_row_count: int = 2) -> None:
        self.calls = 0
        self.source_row_count = source_row_count

    def analyze(
        self,
        execution: QueryExecutionResult,
        context: object,
    ) -> DeterministicAnalyticsResult:
        self.calls += 1
        metric_name = execution.generation.internal_context.metrics[0].name

        summary = AnalyticsMetricSummary(
            metric_name=metric_name,
            unit="brl",
            value_count=2,
            total=Decimal("100"),
            average=Decimal("50"),
            minimum=Decimal("40"),
            maximum=Decimal("60"),
        )

        return DeterministicAnalyticsResult.model_construct(
            analytics_version="1",
            analytics_status="analyzed",
            deterministic=True,
            calculation_scale=4,
            execution_version="1",
            semantic_version="1",
            catalog_version="1",
            source_row_count=self.source_row_count,
            metric_summaries=(summary,),
            rankings=(),
            series=(),
        )


class StubVisualizationEngine:
    def __init__(self) -> None:
        self.calls = 0

    def specify(
        self,
        analytics: DeterministicAnalyticsResult,
    ) -> DeterministicVisualizationResult:
        self.calls += 1
        summary = analytics.metric_summaries[0]

        specification = KPIVisualizationSpec(
            spec_id="kpi-controlled",
            title="Controlled KPI",
            metric_name=summary.metric_name,
            unit=summary.unit,
            value_count=summary.value_count,
            aggregation=summary.aggregation,
            total=summary.total,
            value=summary.total,
            average=summary.average,
            minimum=summary.minimum,
            maximum=summary.maximum,
        )

        return DeterministicVisualizationResult.model_construct(
            visualization_version="1",
            visualization_status="specified",
            deterministic=True,
            analytics_version=analytics.analytics_version,
            execution_version=analytics.execution_version,
            semantic_version=analytics.semantic_version,
            catalog_version=analytics.catalog_version,
            source_row_count=analytics.source_row_count,
            specifications=(specification,),
        )


class StubInsightEngine:
    def __init__(self, *, evidence_metric: str | None = None) -> None:
        self.calls = 0
        self.evidence_metric = evidence_metric

    def generate(
        self,
        analytics: DeterministicAnalyticsResult,
        visualizations: DeterministicVisualizationResult,
    ) -> GroundedInsightResult:
        self.calls += 1
        metric_name = self.evidence_metric or analytics.metric_summaries[0].metric_name

        evidence = InsightEvidenceReference(
            evidence_type="metric_summary",
            metric_name=metric_name,
        )
        claim = GroundedInsightClaim(
            claim_id="claim-0123456789abcdef01234567",
            text="Controlled grounded claim.",
            evidence=(evidence,),
        )

        return GroundedInsightResult.model_construct(
            insight_version="1",
            insight_status="generated",
            grounded=True,
            calculated_by_llm=False,
            analytics_version=analytics.analytics_version,
            visualization_version=visualizations.visualization_version,
            execution_version=analytics.execution_version,
            semantic_version=analytics.semantic_version,
            catalog_version=analytics.catalog_version,
            source_row_count=analytics.source_row_count,
            provider="mock",
            model="mock",
            usage=None,
            summary="Controlled summary.",
            claims=(claim,),
        )


def _components(
    *,
    repair_attempts: int = 1,
    context_available: bool = True,
    validation_status: str = "validated",
    pipeline_failure: bool = False,
    analytics_row_count: int = 2,
    evidence_metric: str | None = None,
) -> tuple[
    StubPipeline,
    StubExecutor,
    StubAnalyticsEngine,
    StubVisualizationEngine,
    StubInsightEngine,
]:
    return (
        StubPipeline(
            repair_attempts=repair_attempts,
            context_available=context_available,
            validation_status=validation_status,
            fail=pipeline_failure,
        ),
        StubExecutor(),
        StubAnalyticsEngine(source_row_count=analytics_row_count),
        StubVisualizationEngine(),
        StubInsightEngine(evidence_metric=evidence_metric),
    )


def _adapter(
    components: tuple[
        StubPipeline,
        StubExecutor,
        StubAnalyticsEngine,
        StubVisualizationEngine,
        StubInsightEngine,
    ],
) -> RealPipelineEvaluationAdapter:
    pipeline, executor, analytics, visualization, insight = components

    return RealPipelineEvaluationAdapter(
        pipeline=pipeline,
        executor=executor,
        analytics_engine=analytics,
        visualization_engine=visualization,
        insight_engine=insight,
    )


def _case(case_id: str) -> EvaluationCase:
    dataset = load_reference_evaluation_dataset()
    return next(case for case in dataset.cases if case.case_id == case_id)


def _observation(
    result: object,
    metric: EvaluationMetricName,
) -> bool:
    return next(
        observation.passed for observation in result.observations if observation.metric is metric
    )


def test_real_adapter_and_runner_generate_successful_report() -> None:
    components = _components()
    pipeline, executor, analytics, visualization, insight = components
    dataset = load_reference_evaluation_dataset()

    report = EvaluationRunner(_adapter(components)).run(dataset)

    assert report.passed is True
    assert pipeline.calls == 4
    assert executor.calls == 4
    assert analytics.calls == 4
    assert visualization.calls == 4
    assert insight.calls == 4


def test_non_allowed_cases_stop_before_sql_generation() -> None:
    components = _components()
    pipeline = components[0]
    adapter = _adapter(components)
    dataset = load_reference_evaluation_dataset()

    cases = (case for case in dataset.cases if case.category is not EvaluationCaseCategory.VALID)

    results = tuple(adapter.execute(case) for case in cases)

    assert pipeline.calls == 0
    assert all(
        result.actual_disposition is result_category.expectation.disposition
        for result, result_category in zip(
            results,
            (case for case in dataset.cases if case.category is not EvaluationCaseCategory.VALID),
            strict=True,
        )
    )
    assert all(all(observation.passed for observation in result.observations) for result in results)


def test_repair_metric_uses_real_generation_attempts() -> None:
    result = _adapter(_components(repair_attempts=1)).execute(
        _case("valid_cancelled_orders_by_channel")
    )

    assert (
        _observation(
            result,
            EvaluationMetricName.REPAIR_SUCCESS_RATE,
        )
        is True
    )


def test_invalid_sql_contract_fails_sql_metric() -> None:
    result = _adapter(_components(validation_status="rejected")).execute(
        _case("valid_revenue_by_region")
    )

    assert (
        _observation(
            result,
            EvaluationMetricName.SQL_VALIDATION_RATE,
        )
        is False
    )


def test_missing_internal_context_fails_downstream_metrics() -> None:
    result = _adapter(_components(context_available=False)).execute(
        _case("valid_revenue_by_region")
    )

    assert (
        _observation(
            result,
            EvaluationMetricName.SQL_VALIDATION_RATE,
        )
        is False
    )
    assert (
        _observation(
            result,
            EvaluationMetricName.CALCULATION_CONSISTENCY,
        )
        is False
    )
    assert (
        _observation(
            result,
            EvaluationMetricName.INSIGHT_FIDELITY,
        )
        is False
    )


def test_row_count_mismatch_fails_calculation_consistency() -> None:
    result = _adapter(_components(analytics_row_count=3)).execute(_case("valid_revenue_by_region"))

    assert (
        _observation(
            result,
            EvaluationMetricName.CALCULATION_CONSISTENCY,
        )
        is False
    )


def test_unknown_evidence_target_fails_insight_fidelity() -> None:
    result = _adapter(_components(evidence_metric="unknown_metric")).execute(
        _case("valid_revenue_by_region")
    )

    assert (
        _observation(
            result,
            EvaluationMetricName.INSIGHT_FIDELITY,
        )
        is False
    )


def test_pipeline_failure_becomes_sanitized_failed_evidence() -> None:
    case = _case("valid_revenue_by_region")
    result = _adapter(_components(pipeline_failure=True)).execute(case)

    assert (
        _observation(
            result,
            EvaluationMetricName.SQL_VALIDATION_RATE,
        )
        is False
    )
    assert case.question not in result.model_dump_json()
    assert "SELECT" not in result.model_dump_json()


def test_unknown_grounding_status_is_rejected_safely() -> None:
    components = _components()

    def invalid_grounder(question: str) -> QuestionGrounding:
        return QuestionGrounding.model_construct(
            semantic_version="1",
            status="unknown",
            normalized_question=question,
            metrics=(),
            dimensions=(),
            values=(),
            tables=(),
            relationships=(),
            business_rules=(),
            matches=(),
        )

    adapter = RealPipelineEvaluationAdapter(
        pipeline=components[0],
        executor=components[1],
        analytics_engine=components[2],
        visualization_engine=components[3],
        insight_engine=components[4],
        grounder=invalid_grounder,
    )
    case = _case("valid_revenue_by_region")

    with pytest.raises(
        PipelineEvaluationAdapterError,
        match="unsupported status",
    ) as captured:
        adapter.execute(case)

    assert case.question not in str(captured.value)


def test_factory_requires_explicit_components() -> None:
    components = _components()

    adapter = create_real_pipeline_evaluation_adapter(
        pipeline=components[0],
        executor=components[1],
        analytics_engine=components[2],
        visualization_engine=components[3],
        insight_engine=components[4],
    )

    assert isinstance(adapter, RealPipelineEvaluationAdapter)


def test_runner_allows_failed_grounding_evidence_for_matching_disposition() -> None:
    from backend.app.evaluation import (
        EvaluationMetricName,
        EvaluationRunner,
        load_reference_evaluation_dataset,
    )

    dataset = load_reference_evaluation_dataset()
    delegate = _adapter(_components())
    target_case_id = dataset.cases[0].case_id

    class ControlledExecutor:
        def execute(self, case: object) -> object:
            result = delegate.execute(case)

            if result.case_id != target_case_id:
                return result

            observations = tuple(
                observation.model_copy(update={"passed": False})
                if observation.metric is EvaluationMetricName.GROUNDING_ACCURACY
                else observation
                for observation in result.observations
            )

            return result.model_copy(update={"observations": observations})

    report = EvaluationRunner(ControlledExecutor()).run(dataset)
    grounding_metric = next(
        metric
        for metric in report.metrics
        if metric.metric is EvaluationMetricName.GROUNDING_ACCURACY
    )

    assert report.passed is False
    assert grounding_metric.numerator == grounding_metric.denominator - 1
    assert grounding_metric.passed is False


def test_runner_rejects_positive_grounding_when_disposition_is_wrong() -> None:
    import pytest

    from backend.app.evaluation import (
        EvaluationDisposition,
        EvaluationMetricName,
        EvaluationRunner,
        EvaluationRunnerError,
        load_reference_evaluation_dataset,
    )

    dataset = load_reference_evaluation_dataset()
    delegate = _adapter(_components())
    target_case_id = dataset.cases[0].case_id

    class InconsistentExecutor:
        def execute(self, case: object) -> object:
            result = delegate.execute(case)

            if result.case_id != target_case_id:
                return result

            observations = tuple(
                observation.model_copy(update={"passed": True})
                if observation.metric is EvaluationMetricName.GROUNDING_ACCURACY
                else observation
                for observation in result.observations
            )

            return result.model_copy(
                update={
                    "actual_disposition": EvaluationDisposition.BLOCK,
                    "observations": observations,
                }
            )

    with pytest.raises(
        EvaluationRunnerError,
        match="grounding classification evidence is inconsistent",
    ):
        EvaluationRunner(InconsistentExecutor()).run(dataset)
