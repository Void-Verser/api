"""GET /v1/download/{id} — scope `download`, resolves a direct download URL
where the provider genuinely supports it. For a hosted MP3/MP4 file use
POST /v1/convert."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request

from app.dependencies import get_resolver, require_permission
from app.services.resolver import MediaResolver

router = APIRouter(prefix="/v1", tags=["download"])


@router.get("/download/{media_id}", summary="Resolve download URL (X-API-Key required)")
async def media_download(
    request: Request,
    media_id: str = Path(..., min_length=1, max_length=512,
                         description="Provider-prefixed id from search, e.g. youtube:dQw4w9WgXcQ"),
    _key=Depends(require_permission("download")),
    resolver: MediaResolver = Depends(get_resolver),
):
    data, provider = await resolver.get_download(media_id)
    request.state.provider = provider
    return {"success": True, "data": data}
