"""GET /v1/stream/{id} — scope `stream`, resolves a temporary playable URL
where the provider genuinely supports it (422 UNSUPPORTED_OPERATION otherwise)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request

from app.dependencies import get_resolver, require_permission
from app.services.resolver import MediaResolver

router = APIRouter(prefix="/v1", tags=["stream"])


@router.get("/stream/{media_id}", summary="Resolve stream URL (X-API-Key required)")
async def media_stream(
    request: Request,
    media_id: str = Path(..., min_length=1, max_length=512,
                         description="Provider-prefixed id from search, e.g. youtube:dQw4w9WgXcQ"),
    _key=Depends(require_permission("stream")),
    resolver: MediaResolver = Depends(get_resolver),
):
    data, provider = await resolver.get_stream(media_id)
    request.state.provider = provider
    return {"success": True, "data": data}
