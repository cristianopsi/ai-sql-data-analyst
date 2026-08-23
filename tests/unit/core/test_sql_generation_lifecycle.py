import json

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas.llm import (
    LLMGenerationRequest,
)
from backend.app.services.grounding_context import (
    GroundingContextService,
)
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)
from backend.app.services.sql_generation import (
    SQLGenerationPipeline,
)
from backend.app.services.sql_validator import (
    SQLValidator,
)
from backend.app.services.text_to_sql import (
    TextToSQLService,
)


class StubDatabasePools:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def sql_payload(
    request: LLMGenerationRequest,
) -> str:
    del request

    return json.dumps(
        {
            "sql": (
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
            ),
            "explanation": ("Approved revenue grouped by region."),
        }
    )


def test_lifespan_publishes_sql_generation_services() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="lifespan-model",
        responder=sql_payload,
    )

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings

        return pools

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        return provider

    application = create_app(
        settings=Settings(
            llm_provider="mock",
            llm_model="lifespan-model",
            max_result_rows=250,
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
    )

    with TestClient(application):
        context_service = application.state.grounding_context_service
        text_to_sql_service = application.state.text_to_sql_service
        sql_validator = application.state.sql_validator

        assert isinstance(
            context_service,
            GroundingContextService,
        )
        assert isinstance(
            text_to_sql_service,
            TextToSQLService,
        )
        assert isinstance(
            sql_validator,
            SQLValidator,
        )
        assert sql_validator.max_result_rows == 250
        assert pools.opened is True

        question = "Qual foi o faturamento por região em 2025?"
        context = context_service.build(question)
        proposal = text_to_sql_service.propose(question)
        validated = sql_validator.validate(
            proposal,
            context,
        )

        assert proposal.validation_status == ("unvalidated")
        assert validated.validation_status == ("validated")
        assert validated.row_limit == 250
        assert validated.sql.endswith("LIMIT 250")
        assert provider.generation_count == 1

    assert provider.is_closed is True
    assert pools.closed is True
    assert application.state.database_ready is False


def test_sql_service_factory_failure_closes_provider() -> None:
    pools_created = False
    provider = DeterministicMockLLMProvider(
        model_name="lifespan-model",
    )

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        nonlocal pools_created
        del settings

        pools_created = True

        return StubDatabasePools()

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        return provider

    def failing_service_factory(
        settings: Settings,
        llm_provider: LLMProvider,
        context_service: GroundingContextService,
    ) -> TextToSQLService:
        del (
            settings,
            llm_provider,
            context_service,
        )

        raise RuntimeError("SQL service construction failed")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        text_to_sql_service_factory=(failing_service_factory),
    )

    with (
        pytest.raises(
            RuntimeError,
            match="service construction failed",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert provider.is_closed is True
    assert pools_created is False
    assert application.state.database_ready is False


def test_lifespan_publishes_sql_generation_pipeline() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="pipeline-model",
        responder=sql_payload,
    )

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings

        return pools

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        return provider

    application = create_app(
        settings=Settings(
            max_result_rows=175,
            max_sql_repair_attempts=2,
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
    )

    with TestClient(application):
        pipeline = application.state.sql_generation_pipeline

        assert isinstance(
            pipeline,
            SQLGenerationPipeline,
        )
        assert pipeline.max_repair_attempts == 2

        result = pipeline.generate("Faturamento por região em 2025")

        assert result.generation_attempts == 1
        assert result.repair_attempts == 0
        assert result.validated_sql.validation_status == "validated"
        assert result.validated_sql.row_limit == 175
        assert provider.generation_count == 1

    assert provider.is_closed is True
    assert pools.closed is True


def test_pipeline_factory_failure_closes_provider() -> None:
    pools_created = False
    provider = DeterministicMockLLMProvider(
        model_name="pipeline-model",
    )

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        nonlocal pools_created
        del settings

        pools_created = True

        return StubDatabasePools()

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        return provider

    def failing_pipeline_factory(
        settings: Settings,
        context_service: GroundingContextService,
        text_to_sql_service: TextToSQLService,
        sql_validator: SQLValidator,
    ) -> SQLGenerationPipeline:
        del (
            settings,
            context_service,
            text_to_sql_service,
            sql_validator,
        )

        raise RuntimeError("SQL pipeline construction failed")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        sql_generation_pipeline_factory=(failing_pipeline_factory),
    )

    with (
        pytest.raises(
            RuntimeError,
            match="pipeline construction failed",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert provider.is_closed is True
    assert pools_created is False
    assert application.state.database_ready is False
