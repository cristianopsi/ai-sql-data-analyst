"""Query execution API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.query_execution import (
    QueryExecutionRequest,
    QueryExecutionResponse,
)
from backend.app.services.query_executor import QueryExecutorService

router = APIRouter(prefix="/api/v1", tags=["query-execution"])


@router.post(
    "/queries/execute",
    response_model=QueryExecutionResponse,
    dependencies=[Depends(require_roles({Role.PAID_USER, Role.ADMIN}))],
)
async def execute_query(request: QueryExecutionRequest) -> QueryExecutionResponse:
    """Execute a validated SQL query in read-only mode."""
    service = QueryExecutorService()
    return service.execute(request)
