"""Grounding API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.grounding import GroundingRequest, GroundingResponse
from backend.app.services.question_grounding import QuestionGroundingService

router = APIRouter(prefix="/api/v1", tags=["grounding"])


@router.post(
    "/grounding",
    response_model=GroundingResponse,
    dependencies=[Depends(require_roles({Role.FREE_USER, Role.PAID_USER, Role.ADMIN}))],
)
async def ground_question(request: GroundingRequest) -> GroundingResponse:
    """Ground a natural language question against the schema."""
    service = QuestionGroundingService()
    return service.ground(request.question)
