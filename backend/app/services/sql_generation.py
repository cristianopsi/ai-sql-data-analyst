from collections.abc import Callable
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
    SQLProposal,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)
from backend.app.services.grounding_context import (
    GroundingContextService,
)
from backend.app.services.sql_validator import (
    SQLValidationError,
    SQLValidator,
)
from backend.app.services.text_to_sql import (
    TextToSQLService,
)


class SQLGenerationExhaustedError(RuntimeError):
    """Raised when every SQL proposal fails validation."""


class SQLGenerationContextBuilder(Protocol):
    def build(
        self,
        question: str,
    ) -> CompactGroundingContext:
        """Build safe context for a question."""


class SQLProposalGenerator(Protocol):
    def propose_from_context(
        self,
        context: CompactGroundingContext,
    ) -> SQLProposal:
        """Generate an initial proposal."""

    def repair(
        self,
        context: CompactGroundingContext,
        rejected_proposal: SQLProposal,
    ) -> SQLProposal:
        """Generate a repaired proposal."""


class SQLProposalValidator(Protocol):
    def validate(
        self,
        proposal: SQLProposal,
        context: CompactGroundingContext,
    ) -> ValidatedSQL:
        """Validate a proposal against its context."""


class SQLGenerationPipeline:
    """Generate, validate, and repair SQL without execution."""

    def __init__(
        self,
        context_builder: SQLGenerationContextBuilder,
        proposal_generator: SQLProposalGenerator,
        validator: SQLProposalValidator,
        *,
        max_repair_attempts: int,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts cannot be negative")

        self._context_builder = context_builder
        self._proposal_generator = proposal_generator
        self._validator = validator
        self._max_repair_attempts = max_repair_attempts

    @property
    def max_repair_attempts(self) -> int:
        return self._max_repair_attempts

    def generate(
        self,
        question: str,
    ) -> SQLGenerationResult:
        context = self._context_builder.build(question)
        proposal = self._proposal_generator.propose_from_context(context)
        generation_attempts = 1
        repair_attempts = 0

        while True:
            try:
                validated = self._validator.validate(
                    proposal,
                    context,
                )

                return SQLGenerationResult(
                    validated_sql=validated,
                    internal_context=context,
                    generation_attempts=(generation_attempts),
                    repair_attempts=(repair_attempts),
                )
            except SQLValidationError:
                if repair_attempts >= self._max_repair_attempts:
                    raise SQLGenerationExhaustedError(
                        "SQL proposal could not be validated"
                    ) from None

                proposal = self._proposal_generator.repair(
                    context,
                    proposal,
                )
                repair_attempts += 1
                generation_attempts += 1


type SQLGenerationPipelineFactory = Callable[
    [
        Settings,
        GroundingContextService,
        TextToSQLService,
        SQLValidator,
    ],
    SQLGenerationPipeline,
]


def create_sql_generation_pipeline(
    settings: Settings,
    context_service: GroundingContextService,
    text_to_sql_service: TextToSQLService,
    sql_validator: SQLValidator,
) -> SQLGenerationPipeline:
    """Create the configured SQL generation pipeline."""
    return SQLGenerationPipeline(
        context_service,
        text_to_sql_service,
        sql_validator,
        max_repair_attempts=(settings.max_sql_repair_attempts),
    )
