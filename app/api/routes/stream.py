"""GET /v1/stream/{id} — scope `stream`, returns a temporary URL."""

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

router = APIRouter(prefix="/v1", tags=["stream"])


@router.get("/stream/{media_id}", summary="Resolve stream URL (X-API-Key required)")
async def stream(
    request: Request,
    media_id: str = Path(..., min_length=1, max_length=512),
    _key=Depends(require_permission("stream")),
    resolver: MediaResolver = Depends(get_resolver),
):
    try:
        data, provider = await resolver.get_stream(media_id)
    except ResolverBadRequest as exc:
        raise APIError(400, "BAD_REQUEST", str(exc)) from exc
    except ResolverNotFound as exc:
        raise APIError(404, "NOT_FOUND", str(exc)) from exc
    except ResolverUnavailable as exc:
        raise APIError(502, "PROVIDER_UNAVAILABLE", str(exc)) from exc
    request.state.provider = provider
    return {"success": True, "data": data}
