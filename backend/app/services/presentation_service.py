"""Controlled orchestration for complete analytical presentations."""

from pydantic import ValidationError

from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationQueryResult,
    PresentationRequest,
)
from backend.app.schemas.query_execution import QueryExecutionResult
from backend.app.schemas.semantic_context import CompactGroundingContext
from backend.app.services.analytics_engine import (
    DeterministicAnalyticsEngine,
)
from backend.app.services.insight_engine import GroundedInsightEngine
from backend.app.services.query_executor import QueryExecutor
from backend.app.services.sql_generation import SQLGenerationPipeline
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
)


class PresentationServiceError(RuntimeError):
    """Base error raised by analytical presentation orchestration."""


class PresentationInputError(PresentationServiceError):
    """Raised when a presentation cannot be safely composed."""


def _project_analytics_context(
    context: CompactGroundingContext,
    execution: QueryExecutionResult,
) -> CompactGroundingContext:
    """Keep only dimensions represented by executed result columns."""
    execution_columns = {column.casefold() for column in execution.columns}
    projected_dimensions = tuple(
        dimension
        for dimension in context.dimensions
        if dimension.name.casefold() in execution_columns
    )

    return context.model_copy(
        update={
            "dimensions": projected_dimensions,
        }
    )


class AnalyticalPresentationService:
    """Run each governed pipeline stage once and compose its trusted results."""

    def __init__(
        self,
        pipeline: SQLGenerationPipeline,
        executor: QueryExecutor,
        analytics_engine: DeterministicAnalyticsEngine,
        visualization_engine: DeterministicVisualizationEngine,
        insight_engine: GroundedInsightEngine,
    ) -> None:
        self._pipeline = pipeline
        self._executor = executor
        self._analytics_engine = analytics_engine
        self._visualization_engine = visualization_engine
        self._insight_engine = insight_engine

    def generate(self, question: str) -> AnalyticalPresentationResult:
        """Generate one presentation from one governed question."""

        try:
            request = PresentationRequest(question=question)
        except ValidationError as error:
            raise PresentationInputError("Presentation question is invalid") from error

        generation = self._pipeline.generate(request.question)
        context = generation.internal_context

        if context is None:
            raise PresentationInputError("Presentation requires trusted grounding context")

        execution = self._executor.execute(generation)

        if execution.transaction_read_only is not True:
            raise PresentationInputError("Presentation requires read-only query execution")

        try:
            query = PresentationQueryResult(
                validated_sql=generation.validated_sql.sql,
                columns=execution.columns,
                rows=execution.rows,
                row_count=execution.row_count,
            )
        except ValidationError as error:
            raise PresentationInputError("Presentation query result is inconsistent") from error

        analytics_context = _project_analytics_context(
            context,
            execution,
        )
        analytics = self._analytics_engine.analyze(
            execution,
            analytics_context,
        )
        visualizations = self._visualization_engine.specify(analytics)
        insights = self._insight_engine.generate(
            analytics,
            visualizations,
        )

        try:
            return AnalyticalPresentationResult(
                source_row_count=analytics.source_row_count,
                query=query,
                visualizations=visualizations,
                insights=insights,
            )
        except ValidationError as error:
            raise PresentationInputError("Presentation results are inconsistent") from error


def create_presentation_service(
    pipeline: SQLGenerationPipeline,
    executor: QueryExecutor,
    analytics_engine: DeterministicAnalyticsEngine,
    visualization_engine: DeterministicVisualizationEngine,
    insight_engine: GroundedInsightEngine,
) -> AnalyticalPresentationService:
    """Create the managed analytical presentation service."""

    return AnalyticalPresentationService(
        pipeline=pipeline,
        executor=executor,
        analytics_engine=analytics_engine,
        visualization_engine=visualization_engine,
        insight_engine=insight_engine,
    )
