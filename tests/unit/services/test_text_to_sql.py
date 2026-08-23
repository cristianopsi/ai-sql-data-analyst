import json

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMGenerationResponse,
    LLMTokenUsage,
)
from backend.app.schemas.sql_generation import (
    SQLProposal,
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
from backend.app.services.text_to_sql import (
    SQL_PROPOSAL_SYSTEM_MESSAGE,
    TextToSQLGroundingError,
    TextToSQLResponseError,
    TextToSQLService,
    TextToSQLUnavailableError,
    create_text_to_sql_service,
)

VALID_SQL = """
SELECT
    r.name AS region,
    SUM(p.amount) AS approved_revenue
FROM retail.payments AS p
JOIN retail.orders AS o
    ON o.id = p.order_id
JOIN retail.regions AS r
    ON r.id = o.region_id
WHERE
    p.status = 'approved'
    AND EXTRACT(YEAR FROM o.placed_at) = 2025
GROUP BY r.name
ORDER BY approved_revenue DESC
""".strip()


def build_context_service() -> GroundingContextService:
    cache = SchemaCatalogCache(
        builder=build_schema_catalog,
        ttl_seconds=300,
    )

    return GroundingContextService(
        cache,
        max_question_length=2_000,
    )


def successful_payload() -> str:
    return json.dumps(
        {
            "sql": VALID_SQL,
            "explanation": ("Aggregates approved payment amounts by the order sales region."),
        }
    )


def test_proposal_uses_safe_grounding_context() -> None:
    captured_requests: list[LLMGenerationRequest] = []

    def responder(
        request: LLMGenerationRequest,
    ) -> str:
        captured_requests.append(request)

        return successful_payload()

    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=responder,
    )
    service = TextToSQLService(
        provider,
        build_context_service(),
        temperature=0.0,
        max_tokens=512,
    )

    proposal = service.propose("Qual foi o faturamento por região em 2025?")

    assert proposal.validation_status == ("unvalidated")
    assert proposal.sql == VALID_SQL
    assert proposal.provider == "mock"
    assert proposal.model == "controlled-model"
    assert proposal.usage.total_tokens > 0

    assert len(captured_requests) == 1
    request = captured_requests[0]

    assert request.response_format == "json"
    assert request.temperature == 0.0
    assert request.max_tokens == 512
    assert tuple(message.role for message in request.messages) == (
        "system",
        "user",
    )
    assert request.messages[0].content == (SQL_PROPOSAL_SYSTEM_MESSAGE)
    assert '"grounding_status":"grounded"' in request.messages[1].content
    assert '"name":"payments"' in request.messages[1].content
    assert "customers.email" not in request.messages[1].content
    assert provider.generation_count == 1

    provider.close()


def test_sql_proposal_is_immutable() -> None:
    proposal = SQLProposal(
        context_version="1",
        semantic_version="1",
        catalog_version="1",
        provider="mock",
        model="controlled-model",
        sql="SELECT 1",
        explanation="Controlled test proposal.",
        usage=LLMTokenUsage(
            input_tokens=1,
            output_tokens=1,
        ),
    )

    with pytest.raises(ValidationError):
        proposal.sql = "SELECT 2"  # type: ignore[misc]


@pytest.mark.parametrize(
    "question",
    [
        "Liste os emails dos clientes",
        "Qual é a temperatura hoje?",
        "Mostre por região",
    ],
)
def test_non_grounded_question_is_rejected_before_llm(
    question: str,
) -> None:
    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=lambda request: successful_payload(),
    )
    service = TextToSQLService(
        provider,
        build_context_service(),
        temperature=0.0,
        max_tokens=512,
    )

    with pytest.raises(
        TextToSQLGroundingError,
        match="cannot be converted",
    ):
        service.propose(question)

    assert provider.generation_count == 0

    provider.close()


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '{"sql":"SELECT 1"}',
        ('{"sql":"SELECT 1","explanation":"test","unexpected":true}'),
    ],
)
def test_invalid_provider_payload_is_rejected(
    content: str,
) -> None:
    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=lambda request: content,
    )
    service = TextToSQLService(
        provider,
        build_context_service(),
        temperature=0.0,
        max_tokens=512,
    )

    with pytest.raises(
        TextToSQLResponseError,
        match="invalid SQL proposal",
    ):
        service.propose("Faturamento por região em 2025")

    provider.close()


class LengthLimitedProvider:
    @property
    def provider_name(self) -> str:
        return "controlled"

    @property
    def model_name(self) -> str:
        return "controlled-model"

    def generate(
        self,
        request: LLMGenerationRequest,
    ) -> LLMGenerationResponse:
        del request

        return LLMGenerationResponse(
            provider=self.provider_name,
            model=self.model_name,
            content=successful_payload(),
            finish_reason="length",
            usage=LLMTokenUsage(
                input_tokens=10,
                output_tokens=10,
            ),
        )


def test_incomplete_provider_response_is_rejected() -> None:
    service = TextToSQLService(
        LengthLimitedProvider(),
        build_context_service(),
        temperature=0.0,
        max_tokens=512,
    )

    with pytest.raises(
        TextToSQLResponseError,
        match="did not complete",
    ):
        service.propose("Faturamento por região em 2025")


def test_provider_failure_is_sanitized() -> None:
    sensitive_detail = "external-provider-secret-detail"

    def failing_responder(
        request: LLMGenerationRequest,
    ) -> str:
        del request

        raise RuntimeError(sensitive_detail)

    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=failing_responder,
    )
    service = TextToSQLService(
        provider,
        build_context_service(),
        temperature=0.0,
        max_tokens=512,
    )

    with pytest.raises(
        TextToSQLUnavailableError,
        match="provider is unavailable",
    ) as captured:
        service.propose("Faturamento por região em 2025")

    assert sensitive_detail not in str(captured.value)

    provider.close()


def test_factory_uses_generation_settings() -> None:
    captured_requests: list[LLMGenerationRequest] = []

    def responder(
        request: LLMGenerationRequest,
    ) -> str:
        captured_requests.append(request)

        return successful_payload()

    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=responder,
    )
    settings = Settings(
        llm_temperature=0.25,
        llm_max_tokens=321,
    )

    service = create_text_to_sql_service(
        settings,
        provider,
        build_context_service(),
    )

    proposal = service.propose("Faturamento por região em 2025")

    assert service.temperature == 0.25
    assert service.max_tokens == 321
    assert proposal.validation_status == ("unvalidated")
    assert captured_requests[0].temperature == (0.25)
    assert captured_requests[0].max_tokens == 321

    provider.close()
