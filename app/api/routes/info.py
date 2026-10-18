"""GET /v1/info/{id} — scope `info`, normalized metadata."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request

from app.dependencies import get_resolver, require_permission
from app.services.resolver import MediaResolver

router = APIRouter(prefix="/v1", tags=["info"])


@router.get("/info/{media_id}", summary="Media metadata (X-API-Key required)")
async def media_info(
    request: Request,
    media_id: str = Path(..., min_length=1, max_length=512,
                         description="Provider-prefixed id from search, e.g. youtube:dQw4w9WgXcQ"),
    _key=Depends(require_permission("info")),
    resolver: MediaResolver = Depends(get_resolver),
):
    data, provider = await resolver.get_info(media_id)
    request.state.provider = provider
    return {"success": True, "data": data}
