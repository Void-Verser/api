"""FastAPI application entrypoint.

Run:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
Production (Railway honors $PORT automatically via the start command):
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.admin.keys import router as admin_router
from app.api.routes.download import router as download_router
from app.api.routes.health import router as health_router
from app.api.routes.info import router as info_router
from app.api.routes.search import router as search_router
from app.api.routes.stream import router as stream_router
from app.config import get_settings
from app.database import init_db
from app.middleware.logging import LoggingMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.schemas.errors import APIError, error_payload

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

app = FastAPI(
    title="Media API",
    version=__version__,
    description=(
        "Centralized media API for Telegram bots and other clients.\n\n"
        "Authentication: send your key as `X-API-Key: mapi_...` "
        "(configure clients with `MEDIA_API_URL` + `MEDIA_API_KEY`).\n\n"
        "Permissions (scopes): `search`, `info`, `stream`, `download`. "
        "A key without the required scope receives HTTP 403.\n\n"
        "Rate limits: per-key, per-minute (HTTP 429 when exceeded). "
        "Every response carries `X-Request-ID` for support/debugging.\n\n"
        "Example:\n\n"
        "    curl -H \"X-API-Key: YOUR_MEDIA_API_KEY\" "
        "\"https://api.YOUR_DOMAIN.com/v1/search?q=Raiden%20Shogun\"\n"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# --- Middleware -------------------------------------------------------------
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Exception handlers -----------------------------------------------------
@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, rid),
        headers={"X-Request-ID": rid} if rid else None,
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):  # noqa: BLE001
    rid = getattr(request.state, "request_id", None)
    logging.getLogger("media_api").exception("unhandled error rid=%s: %s", rid, exc)
    message = "Internal server error." if settings.is_production else f"Internal error: {exc}"
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", message, rid),
        headers={"X-Request-ID": rid} if rid else None,
    )


# --- Routes -----------------------------------------------------------------
app.include_router(health_router)
app.include_router(search_router)
app.include_router(info_router)
app.include_router(stream_router)
app.include_router(download_router)
app.include_router(admin_router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    try:
        from app.services.cache import get_cache
        from app.services.rate_limit import get_rate_limiter

        await get_cache().close()
        await get_rate_limiter().close()
    except Exception:
        pass
