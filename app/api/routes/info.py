"""GET /v1/info/{id} — scope `info`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request

from app.dependencies import get_resolver, require_permission
from app.schemas.errors import APIError
from app.services.resolver import (
    MediaResolver,
    ResolverBadRequest,
    ResolverNotFound,
    ResolverUnavailable,
)

router = APIRouter(prefix="/v1", tags=["info"])


@router.get("/info/{media_id}", summary="Media metadata (X-API-Key required)")
async def media_info(
    request: Request,
    media_id: str = Path(..., min_length=1, max_length=512),
    _key=Depends(require_permission("info")),
    resolver: MediaResolver = Depends(get_resolver),
):
    try:
        info, provider = await resolver.get_info(media_id)
    except ResolverBadRequest as exc:
        raise APIError(400, "BAD_REQUEST", str(exc)) from exc
    except ResolverNotFound as exc:
        raise APIError(404, "NOT_FOUND", str(exc)) from exc
    except ResolverUnavailable as exc:
        raise APIError(503, "PROVIDER_UNAVAILABLE", str(exc)) from exc
    request.state.provider = provider
    return {"success": True, "data": info}
