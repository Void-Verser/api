"""GET /v1/search — authenticated, scope `search`."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import get_resolver, require_permission
from app.services.resolver import MediaResolver

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
    provider: Optional[str] = Query(
        None, description="Search one provider only: youtube, soundcloud, spotify, apple_music. "
        "Default: configured order, YouTube first.",
    ),
    _key=Depends(require_permission("search")),
    resolver: MediaResolver = Depends(get_resolver),
):
    results, served_by = await resolver.search(
        q, limit=limit, offset=offset, provider=(provider or "").strip().lower() or None
    )
    request.state.provider = served_by or "-"
    return {"success": True, "data": {"query": q, "results": results, "provider": served_by}}
