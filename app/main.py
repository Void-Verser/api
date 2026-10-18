"""FastAPI application entrypoint (also Vercel's zero-config entrypoint).

Run:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
Container hosts:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.admin.keys import router as admin_router
from app.api.dashboard import router as dashboard_router
from app.api.routes.download import router as download_router
from app.api.routes.health import router as health_router
from app.api.routes.info import router as info_router
from app.api.routes.media import router as media_router
from app.api.routes.search import router as search_router
from app.api.routes.stream import router as stream_router
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.middleware.logging import LoggingMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.schemas.errors import APIError, error_payload
from app.services.resolver import ResolverError

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("media_api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.auth.owner_auth import ensure_bootstrap_owner

    init_db()
    db = get_session_factory()()
    try:
        ensure_bootstrap_owner(db, get_settings())
    finally:
        db.close()
    stop = None
    s = get_settings()
    if s.worker_embedded and not s.on_vercel:
        from app.worker.worker import start_embedded_worker

        stop = start_embedded_worker()
        log.info("embedded worker started")
    yield
    if stop is not None:
        stop.set()
    try:
        from app.services.cache import get_cache
        from app.services.rate_limit import get_rate_limiter

        await get_cache().close()
        await get_rate_limiter().close()
    except Exception:
        pass


app = FastAPI(
    title="Media API",
    version=__version__,
    lifespan=lifespan,
    description=(
        "YouTube-first media API for Telegram bots and other clients.\n\n"
        "**Auth:** one key per bot — `X-API-Key: mapi_...` (create it in `/dashboard`). "
        "Configure clients with `MEDIA_API_URL` + `MEDIA_API_KEY`. New keys cover every "
        "client operation (`*`).\n\n"
        "**Flow for bots:** `GET /v1/search` → `POST /v1/convert` (mp3/mp4) → poll "
        "`GET /v1/jobs/{job_id}` → download `result.url` (expiring).\n\n"
        "**IDs** are provider-prefixed (`youtube:dQw4w9WgXcQ`, `spotify:...`). "
        "Spotify and Apple Music are catalog-only (metadata/links, no audio).\n\n"
        "**Errors:** `{success:false, error:{code, message, request_id}}`. 422 "
        "`UNSUPPORTED_OPERATION` = the provider genuinely cannot do it. 429 = rate limit.\n\n"
        "Every response carries `X-Request-ID`."
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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/dashboard"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- Exception handlers -----------------------------------------------------
@app.exception_handler(ResolverError)
async def resolver_error_handler(request: Request, exc: ResolverError):
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status,
        content=error_payload(exc.code, str(exc), rid),
        headers={"X-Request-ID": rid} if rid else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    rid = getattr(request.state, "request_id", None)
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", []) if x not in ("body", "query", "path"))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return JSONResponse(
        status_code=422,
        content=error_payload("VALIDATION_ERROR", "; ".join(parts) or "Invalid request.", rid),
        headers={"X-Request-ID": rid} if rid else None,
    )


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
app.include_router(media_router)
app.include_router(admin_router)
app.include_router(dashboard_router)

