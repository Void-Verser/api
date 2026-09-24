"""Public health endpoint — no API key, never gated on providers."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import __version__
from app.config import Settings, get_settings
from app.database import check_db
from app.providers.manager import ProviderManager
from app.dependencies import get_manager
from app.services.cache import CacheService, get_cache

router = APIRouter(tags=["health"])


@router.get("/health", summary="Service health (no auth)")
async def health(
    settings: Settings = Depends(get_settings),
    manager: ProviderManager = Depends(get_manager),
    cache: CacheService = Depends(get_cache),
):
    db_ok = check_db()
    cache_ok = await cache.ping()
    providers = [
        {"name": s.name, "enabled": s.enabled, "reason": s.reason}
        for s in manager.status()
    ]
    degraded = not db_ok  # cache has memory fallback; providers may be down
    return {
        "status": "degraded" if degraded else "ok",
        "version": __version__,
        "env": settings.app_env,
        "checks": {"database": "up" if db_ok else "down", "cache": "up" if cache_ok else "down"},
        "providers": providers,
    }
