from hashlib import sha256
from typing import cast

from fastapi import (
    APIRouter,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from backend.app.schemas.catalog import (
    CatalogUnavailableResponse,
    SchemaCatalog,
)
from backend.app.services.catalog_cache import (
    SchemaCatalogCache,
)

router = APIRouter(
    prefix="/api/v1/schema",
    tags=["schema"],
)


def _catalog_etag(catalog: SchemaCatalog) -> str:
    serialized = catalog.model_dump_json()

    digest = sha256(serialized.encode("utf-8")).hexdigest()

    return f'"{digest}"'


def _catalog_headers(
    catalog: SchemaCatalog,
    cache: SchemaCatalogCache,
) -> dict[str, str]:
    return {
        "Cache-Control": (f"private, max-age={max(0, int(cache.ttl_seconds))}"),
        "ETag": _catalog_etag(catalog),
        "X-Catalog-Generation": str(cache.generation),
        "X-Catalog-Version": (catalog.catalog_version),
    }


def _unavailable_response() -> JSONResponse:
    response = CatalogUnavailableResponse()

    return JSONResponse(
        status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
        content=response.model_dump(mode="json"),
    )


@router.get(
    "/catalog",
    response_model=SchemaCatalog,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_304_NOT_MODIFIED: {
            "description": ("The client already has the current catalog representation."),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": CatalogUnavailableResponse,
            "description": ("The safe schema catalog could not be constructed."),
        },
    },
    summary="Get the safe SQL schema catalog",
)
def get_schema_catalog(
    request: Request,
) -> Response:
    cache_value = getattr(
        request.app.state,
        "schema_catalog_cache",
        None,
    )

    if cache_value is None:
        return _unavailable_response()

    cache = cast(
        SchemaCatalogCache,
        cache_value,
    )

    try:
        catalog = cache.get()
    except Exception:
        return _unavailable_response()

    headers = _catalog_headers(
        catalog,
        cache,
    )
    request_etag = request.headers.get("if-none-match")

    if request_etag == headers["ETag"]:
        return Response(
            status_code=(status.HTTP_304_NOT_MODIFIED),
            headers=headers,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=catalog.model_dump(mode="json"),
        headers=headers,
    )
