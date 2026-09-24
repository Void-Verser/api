"""GET /v1/download/{id} — scope `download`."""

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

router = APIRouter(prefix="/v1", tags=["download"])


@router.get("/download/{media_id}", summary="Resolve download URL (X-API-Key required)")
async def download(
    request: Request,
    media_id: str = Path(..., min_length=1, max_length=512),
    _key=Depends(require_permission("download")),
    resolver: MediaResolver = Depends(get_resolver),
):
    try:
        data, provider = await resolver.get_download(media_id)
    except ResolverBadRequest as exc:
        raise APIError(400, "BAD_REQUEST", str(exc)) from exc
    except ResolverNotFound as exc:
        raise APIError(404, "NOT_FOUND", str(exc)) from exc
    except ResolverUnavailable as exc:
        raise APIError(502, "PROVIDER_UNAVAILABLE", str(exc)) from exc
    request.state.provider = provider
    return {"success": True, "data": data}
