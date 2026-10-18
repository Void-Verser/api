"""GET /v1/search — authenticated, scope `search`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import get_resolver, require_permission
from app.schemas.errors import APIError
from app.services.resolver import (
    MediaResolver,
    ResolverBadRequest,
    ResolverNotFound,
    ResolverUnavailable,
)

router = APIRouter(prefix="/v1", tags=["search"])


@router.get(
    "/search",
    summary="Search media (X-API-Key required)",
    responses={401: {"description": "Unauthorized"}, 403: {"description": "Forbidden"}},
)
async def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    _key=Depends(require_permission("search")),
    resolver: MediaResolver = Depends(get_resolver),
):
    try:
        results, provider = await resolver.search(q, limit=limit, offset=offset)
    except ResolverBadRequest as exc:
        raise APIError(400, "BAD_REQUEST", str(exc)) from exc
    except ResolverNotFound as exc:
        raise APIError(404, "NOT_FOUND", str(exc)) from exc
    except ResolverUnavailable as exc:
        raise APIError(503, "PROVIDER_UNAVAILABLE", str(exc)) from exc
    request.state.provider = provider or "-"
    return {"success": True, "data": {"query": q, "results": results, "provider": provider}}
