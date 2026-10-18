"""Public health endpoints — no API key, never gated on providers.

/health        liveness + summary (always 200 so platforms keep routing)
/health/ready  readiness: 503 when the database is down
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, get_settings
from app.database import check_db, get_session_factory, is_persistent_database
from app.dependencies import get_manager
from app.providers.manager import ProviderManager
from app.services import jobs as jobsvc
from app.services.cache import CacheService, get_cache
from app.services.storage import get_storage

router = APIRouter(tags=["health"])


async def _report(settings: Settings, manager: ProviderManager, cache: CacheService) -> dict:
    db_ok = check_db()
    cache_ok = await cache.ping()
    storage_ok, _ = get_storage(settings).healthy()
    workers = 0
    if db_ok:
        db = get_session_factory()()
        try:
            workers = len(jobsvc.workers_online(db, within_seconds=120))
        except Exception:
            workers = 0
        finally:
            db.close()
    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "env": settings.app_env,
        "checks": {
            "database": "up" if db_ok else "down",
            "database_persistent": is_persistent_database() if db_ok else False,
            "cache": "up" if cache_ok else "down",
            "storage": "up" if storage_ok else "not_configured",
            "workers_online": workers,
            "media_extraction": "enabled" if settings.media_extraction_enabled else "disabled",
        },
        "providers": [
            {"name": s.name, "enabled": s.enabled, "capabilities": s.capabilities, "reason": s.reason}
            for s in manager.status()
        ],
    }


@router.get("/health", summary="Service health (no auth)")
async def health(
    settings: Settings = Depends(get_settings),
    manager: ProviderManager = Depends(get_manager),
    cache: CacheService = Depends(get_cache),
):
    return await _report(settings, manager, cache)


@router.get("/health/ready", summary="Readiness (503 if the database is down)")
async def ready(
    settings: Settings = Depends(get_settings),
    manager: ProviderManager = Depends(get_manager),
    cache: CacheService = Depends(get_cache),
):
    report = await _report(settings, manager, cache)
    return JSONResponse(status_code=200 if report["status"] == "ok" else 503, content=report)
