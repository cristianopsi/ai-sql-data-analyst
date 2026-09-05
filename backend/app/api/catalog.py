"""Catalog API router."""

from fastapi import APIRouter, Depends

from backend.app.core.roles import Role, require_roles
from backend.app.schemas.catalog import CatalogResponse
from backend.app.services.schema_catalog import SchemaCatalogService

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    dependencies=[Depends(require_roles({Role.FREE_USER, Role.PAID_USER, Role.ADMIN}))],
)
async def get_catalog() -> CatalogResponse:
    """Return the schema catalog for the retail analytics database."""
    service = SchemaCatalogService()
    return service.get_catalog()
