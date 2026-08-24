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
from backend.app.services.sql_generation import (
    SQLGenerationPipeline,
)


class StubContextBuilder:
    def __init__(
        self,
        context: CompactGroundingContext,
    ) -> None:
        self.context = context
        self.calls = 0

    def build(
        self,
        question: str,
    ) -> CompactGroundingContext:
        assert question == "Receita aprovada por região"
        self.calls += 1
        return self.context


class StubProposalGenerator:
    def __init__(
        self,
        expected_context: CompactGroundingContext,
    ) -> None:
        self.expected_context = expected_context
        self.proposal_calls = 0
        self.repair_calls = 0

    def propose_from_context(
        self,
        context: CompactGroundingContext,
    ) -> SQLProposal:
        assert context is self.expected_context
        self.proposal_calls += 1
        return SQLProposal.model_construct()

    def repair(
        self,
        context: CompactGroundingContext,
        rejected_proposal: SQLProposal,
    ) -> SQLProposal:
        del context
        del rejected_proposal
        self.repair_calls += 1
        raise AssertionError("A valid proposal must not enter repair")


class StubValidator:
    def __init__(
        self,
        expected_context: CompactGroundingContext,
    ) -> None:
        self.expected_context = expected_context
        self.calls = 0

    def validate(
        self,
        proposal: SQLProposal,
        context: CompactGroundingContext,
    ) -> ValidatedSQL:
        del proposal
        assert context is self.expected_context
        self.calls += 1
        return ValidatedSQL.model_construct()


def test_pipeline_transports_context_once_without_public_exposure() -> None:
    context = CompactGroundingContext(
        semantic_version="1",
        catalog_version="1",
        grounding_status="grounded",
        normalized_question="receita aprovada por região",
    )
    context_builder = StubContextBuilder(context)
    proposal_generator = StubProposalGenerator(context)
    validator = StubValidator(context)
    pipeline = SQLGenerationPipeline(
        context_builder,
        proposal_generator,
        validator,
        max_repair_attempts=0,
    )

    result = pipeline.generate(
        "Receita aprovada por região",
    )

    assert result.internal_context is context
    assert context_builder.calls == 1
    assert proposal_generator.proposal_calls == 1
    assert proposal_generator.repair_calls == 0
    assert validator.calls == 1
    assert "internal_context" not in result.model_dump(
        mode="json",
    )
    assert (
        "internal_context"
        not in SQLGenerationResult.model_json_schema(
            mode="serialization",
        )["properties"]
    )
    assert (
        "internal_context"
        in SQLGenerationResult.model_json_schema(
            mode="validation",
        )["properties"]
    )
