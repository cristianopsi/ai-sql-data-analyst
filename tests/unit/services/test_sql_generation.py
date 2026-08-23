import json

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMGenerationRequest,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)
from backend.app.services.catalog_cache import (
    SchemaCatalogCache,
)
from backend.app.services.grounding_context import (
    GroundingContextService,
)
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
    SQLGenerationPipeline,
    create_sql_generation_pipeline,
)
from backend.app.services.sql_validator import (
    SQLValidator,
)
from backend.app.services.text_to_sql import (
    SQL_REPAIR_SYSTEM_MESSAGE,
    TextToSQLGroundingError,
    TextToSQLService,
)

SAFE_SQL = (
    "SELECT "
    "r.name AS region, "
    "SUM(p.amount) AS approved_revenue "
    "FROM retail.payments AS p "
    "JOIN retail.orders AS o "
    "ON o.id = p.order_id "
    "JOIN retail.regions AS r "
    "ON r.id = o.region_id "
    "WHERE p.status = 'approved' "
    "GROUP BY r.name"
)


def payload(
    sql: str,
) -> str:
    return json.dumps(
        {
            "sql": sql,
            "explanation": ("Controlled SQL generation."),
        }
    )


def context_service() -> GroundingContextService:
    return GroundingContextService(
        SchemaCatalogCache(
            builder=build_schema_catalog,
            ttl_seconds=300,
        ),
        max_question_length=2_000,
    )


def build_pipeline(
    responses: list[str],
    *,
    max_repair_attempts: int,
) -> tuple[
    SQLGenerationPipeline,
    DeterministicMockLLMProvider,
    list[LLMGenerationRequest],
]:
    captured: list[LLMGenerationRequest] = []

    def responder(
        request: LLMGenerationRequest,
    ) -> str:
        captured.append(request)
        response_index = len(captured) - 1

        if response_index >= len(responses):
            return responses[-1]

        return responses[response_index]

    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=responder,
    )
    grounding = context_service()
    proposal_service = TextToSQLService(
        provider,
        grounding,
        temperature=0.0,
        max_tokens=512,
    )
    validator = SQLValidator(
        max_result_rows=100,
    )

    return (
        SQLGenerationPipeline(
            grounding,
            proposal_service,
            validator,
            max_repair_attempts=(max_repair_attempts),
        ),
        provider,
        captured,
    )


def test_valid_first_proposal_requires_no_repair() -> None:
    pipeline, provider, captured = build_pipeline(
        [
            payload(SAFE_SQL),
        ],
        max_repair_attempts=2,
    )

    result = pipeline.generate("Faturamento por região em 2025")

    assert result.generation_attempts == 1
    assert result.repair_attempts == 0
    assert result.validated_sql.row_limit == 100
    assert provider.generation_count == 1
    assert len(captured) == 1

    provider.close()


def test_invalid_proposal_is_repaired() -> None:
    pipeline, provider, captured = build_pipeline(
        [
            payload("SELECT * FROM retail.orders"),
            payload(SAFE_SQL),
        ],
        max_repair_attempts=2,
    )

    result = pipeline.generate("Faturamento por região em 2025")

    assert result.generation_attempts == 2
    assert result.repair_attempts == 1
    assert result.validated_sql.validation_status == "validated"
    assert provider.generation_count == 2
    assert len(captured) == 2
    assert captured[1].messages[0].content == (SQL_REPAIR_SYSTEM_MESSAGE)
    assert '"rejected_sql":' in captured[1].messages[1].content
    assert "SELECT * FROM retail.orders" in captured[1].messages[1].content

    provider.close()


def test_repair_budget_exhaustion_is_sanitized() -> None:
    rejected_sql = "SELECT * FROM retail.orders"
    pipeline, provider, _ = build_pipeline(
        [
            payload(rejected_sql),
        ],
        max_repair_attempts=2,
    )

    with pytest.raises(
        SQLGenerationExhaustedError,
        match="could not be validated",
    ) as captured:
        pipeline.generate("Faturamento por região em 2025")

    assert rejected_sql not in str(captured.value)
    assert provider.generation_count == 3

    provider.close()


def test_zero_repair_budget_fails_after_initial_proposal() -> None:
    pipeline, provider, _ = build_pipeline(
        [
            payload("DELETE FROM retail.orders"),
        ],
        max_repair_attempts=0,
    )

    with pytest.raises(
        SQLGenerationExhaustedError,
        match="could not be validated",
    ):
        pipeline.generate("Faturamento por região em 2025")

    assert provider.generation_count == 1

    provider.close()


def test_restricted_question_never_calls_provider() -> None:
    pipeline, provider, _ = build_pipeline(
        [
            payload(SAFE_SQL),
        ],
        max_repair_attempts=2,
    )

    with pytest.raises(
        TextToSQLGroundingError,
        match="cannot be converted",
    ):
        pipeline.generate("Liste os emails dos clientes")

    assert provider.generation_count == 0

    provider.close()


def test_pipeline_factory_uses_repair_setting() -> None:
    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=lambda request: payload(SAFE_SQL),
    )
    grounding = context_service()
    proposal_service = TextToSQLService(
        provider,
        grounding,
        temperature=0.0,
        max_tokens=512,
    )
    validator = SQLValidator(
        max_result_rows=100,
    )

    pipeline = create_sql_generation_pipeline(
        Settings(
            max_sql_repair_attempts=4,
        ),
        grounding,
        proposal_service,
        validator,
    )

    assert pipeline.max_repair_attempts == 4

    provider.close()


def test_generation_result_is_immutable() -> None:
    pipeline, provider, _ = build_pipeline(
        [
            payload(SAFE_SQL),
        ],
        max_repair_attempts=2,
    )

    result = pipeline.generate("Faturamento por região em 2025")

    assert isinstance(
        result,
        SQLGenerationResult,
    )

    with pytest.raises(ValidationError):
        result.repair_attempts = 99  # type: ignore[misc]

    provider.close()
