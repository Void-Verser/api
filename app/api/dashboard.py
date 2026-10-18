"""Owner dashboard: HTML shell + JSON API (/dashboard/api/*).

The browser never receives MASTER_API_SECRET or any provider credential.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.auth.owner_auth import end_session, require_owner, start_session
from app.config import Settings, get_settings
from app.database import get_db, is_persistent_database
from app.dependencies import get_manager
from app.models.api_key import ApiKey
from app.models.owner import Owner
from app.providers.manager import ProviderManager
from app.schemas.errors import APIError
from app.services import jobs as jobsvc
from app.services.keys import create_key, public_key, rotate_key
from app.services.public_url import public_base_url
from app.services.rate_limit import RateLimiter, get_rate_limiter
from app.services.storage import get_storage
from app.utils.security import verify_password

STATIC = Path(__file__).resolve().parent.parent / "dashboard"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
ASSETS = {"app.js": "text/javascript", "styles.css": "text/css"}

router = APIRouter(tags=["dashboard"], include_in_schema=False)


def _static(name: str, media_type: str) -> FileResponse:
    return FileResponse(
        STATIC / name, media_type=media_type,
        headers={"Content-Security-Policy": CSP, "X-Frame-Options": "DENY",
                 "Cache-Control": "no-cache"},
    )


@router.get("/")
def root(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/dashboard", status_code=307)
    return {"success": True, "data": {"name": "Media API", "version": __version__,
                                      "docs": "/docs", "health": "/health", "dashboard": "/dashboard"}}


@router.get("/dashboard")
def dashboard_page():
    return _static("index.html", "text/html; charset=utf-8")


@router.get("/dashboard/assets/{name}")
def dashboard_asset(name: str):
    if name not in ASSETS:
        raise APIError(404, "NOT_FOUND", "Not found.")
    return _static(name, ASSETS[name])


# --- JSON API ---------------------------------------------------------------
class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class NewKeyBody(BaseModel):
    name: str = Field(default="", max_length=128)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


@router.post("/dashboard/api/login")
async def login(
    request: Request, response: Response, body: LoginBody,
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    allowed, _, reset = await limiter.is_allowed(
        f"login:{_client_ip(request)}", settings.login_attempts_per_minute
    )
    if allowed:  # X-Forwarded-For can be spoofed, so also throttle per account
        allowed, _, reset = await limiter.is_allowed(
            f"login-email:{body.email.strip().lower()}", settings.login_attempts_per_minute
        )
    if not allowed:
        raise APIError(429, "RATE_LIMITED", f"Too many sign-in attempts. Retry in {reset}s.")
    owner =db.scalar(select(Owner).where(Owner.email == body.email.strip().lower()))
    # Always run a hash comparison to keep timing uniform.
    ok = verify_password(body.password, owner.password_hash if owner else
                         "scrypt$16384$8$1$00$" + "0" * 64)
    if not owner or not ok:
        raise APIError(401, "INVALID_CREDENTIALS", "Incorrect email or password.")
    sess = start_session(db, owner, response, request, settings)
    return {"success": True, "data": {"email": owner.email, "csrf_token": sess.csrf_token}}


@router.post("/dashboard/api/logout")
def logout(request: Request, response: Response, _=Depends(require_owner),
           db: Session = Depends(get_db)):
    end_session(db, request, response)
    return {"success": True, "data": {"signed_out": True}}


@router.get("/dashboard/api/session")
def session_info(request: Request, db: Session = Depends(get_db)):
    from app.auth.owner_auth import current_session

    found = current_session(request, db)
    owner_configured = db.scalar(select(Owner.id).limit(1)) is not None
    if not found:
        return {"success": True, "data": {"authenticated": False, "owner_configured": owner_configured}}
    sess, owner = found
    return {"success": True, "data": {"authenticated": True, "owner_configured": True,
                                      "email": owner.email, "csrf_token": sess.csrf_token}}


@router.get("/dashboard/api/overview")
async def overview(
    request: Request, _=Depends(require_owner), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings), manager: ProviderManager = Depends(get_manager),
):
    base, source = public_base_url(settings, request)
    storage = get_storage(settings)
    storage_ok, storage_detail = storage.healthy()
    workers = jobsvc.workers_online(db, within_seconds=120)
    warnings = []
    if not is_persistent_database():
        warnings.append("Database is not persistent (sqlite on ephemeral storage). Set DATABASE_URL.")
    if settings.mock_enabled:
        warnings.append("Mock provider is enabled — fake results are labelled 'mock'. Disable in production.")
    if source == "request":
        warnings.append("Public URL is inferred from this request. Set API_BASE_URL for a stable value.")
    if not settings.signing_secret:
        warnings.append("SIGNING_SECRET is not set; result links depend on MASTER_API_SECRET or a per-process key.")
    if settings.media_extraction_enabled and not workers:
        warnings.append("Conversion is enabled but no worker is online; jobs will queue.")
    if settings.media_extraction_enabled and storage.name == "local" and settings.on_vercel:
        warnings.append("Local result storage does not work on Vercel; configure STORAGE_BACKEND=s3.")
    total = db.scalar(select(func.count(ApiKey.id))) or 0
    active = db.scalar(select(func.count(ApiKey.id)).where(ApiKey.active.is_(True))) or 0
    reqs = db.scalar(select(func.coalesce(func.sum(ApiKey.request_count), 0))) or 0
    return {"success": True, "data": {
        "version": __version__,
        "environment": settings.app_env,
        "api_base_url": base,
        "api_base_url_source": source,
        "stats": {"total_keys": total, "active_keys": active, "total_requests": int(reqs),
                  "jobs": jobsvc.job_counts(db)},
        "providers": [
            {"name": s.name, "enabled": s.enabled, "capabilities": s.capabilities, "note": s.reason}
            for s in manager.status()
        ],
        "processing": {
            "extraction_enabled": settings.media_extraction_enabled,
            "storage_backend": storage.name, "storage_ok": storage_ok, "storage_detail": storage_detail,
            "workers_online": workers,
        },
        "persistent_database": is_persistent_database(),
        "warnings": warnings,
    }}


@router.get("/dashboard/api/keys")
def list_keys(_=Depends(require_owner), db: Session = Depends(get_db)):
    keys = db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return {"success": True, "data": {"keys": [public_key(k) for k in keys]}}


@router.post("/dashboard/api/keys")
def new_key(body: NewKeyBody, auth=Depends(require_owner), db: Session = Depends(get_db)):
    _, owner = auth
    record, plaintext = create_key(db, name=body.name.strip() or "Unnamed bot", owner_id=owner.id)
    return {"success": True, "data": {**public_key(record), "api_key": plaintext}}


def _get_key(db: Session, key_id: int) -> ApiKey:
    record = db.get(ApiKey, key_id)
    if record is None:
        raise APIError(404, "NOT_FOUND", "API key not found.")
    return record


@router.post("/dashboard/api/keys/{key_id}/revoke")
def revoke(key_id: int, _=Depends(require_owner), db: Session = Depends(get_db)):
    record = _get_key(db, key_id)
    record.active = False
    db.commit()
    return {"success": True, "data": public_key(record)}


@router.post("/dashboard/api/keys/{key_id}/rotate")
def rotate(key_id: int, _=Depends(require_owner), db: Session = Depends(get_db)):
    new, plaintext = rotate_key(db, _get_key(db, key_id))
    return {"success": True, "data": {**public_key(new), "api_key": plaintext, "replaced_key_id": key_id}}
