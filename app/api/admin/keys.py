"""Admin key-management endpoints — X-Admin-Key ONLY (never MEDIA_API_KEY)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.admin_auth import require_admin
from app.database import get_db
from app.models.api_key import VALID_PERMISSIONS, ApiKey
from app.schemas.errors import APIError
from app.schemas.admin import CreateKeyRequest, UpdateKeyRequest
from app.utils.security import generate_api_key, hash_api_key

router = APIRouter(prefix="/admin", tags=["admin"])


def _public(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "masked_key": key.masked,
        "active": key.active,
        "permissions": key.permissions or [],
        "rate_limit": key.rate_limit,
        "request_count": key.request_count or 0,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
    }


def _validate_permissions(perms: list[str]) -> list[str]:
    cleaned = sorted({p.strip().lower() for p in (perms or []) if p.strip()})
    unknown = [p for p in cleaned if p not in VALID_PERMISSIONS]
    if unknown:
        raise APIError(400, "BAD_REQUEST", f"Unknown permissions: {', '.join(unknown)}")
    if not cleaned:
        raise APIError(400, "BAD_REQUEST", "At least one permission is required.")
    return cleaned


@router.post("/keys", summary="Create API key (admin)")
def create_key(request: Request, body: CreateKeyRequest, db: Session = Depends(get_db)):
    require_admin(request)
    perms = _validate_permissions(body.permissions)
    plaintext = generate_api_key()
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    record = ApiKey(
        name=body.name,
        key_hash=hash_api_key(plaintext),
        key_last3=plaintext[-3:],
        active=True,
        expires_at=expires_at,
        permissions=perms,
        rate_limit=body.rate_limit,
        request_count=0,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    # Plaintext is returned ONLY here, never stored, never listed again.
    return {"success": True, "data": {**_public(record), "api_key": plaintext}}


@router.get("/keys", summary="List API keys (masked, admin)")
def list_keys(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    keys = db.query(ApiKey).order_by(ApiKey.id).all()
    return {"success": True, "data": {"keys": [_public(k) for k in keys]}}


@router.get("/keys/{key_id}", summary="Get one key (admin)")
def get_key(request: Request, key_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not record:
        raise APIError(404, "NOT_FOUND", "API key not found.")
    return {"success": True, "data": _public(record)}


@router.patch("/keys/{key_id}", summary="Update key (admin)")
def update_key(request: Request, key_id: int, body: UpdateKeyRequest, db: Session = Depends(get_db)):
    require_admin(request)
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not record:
        raise APIError(404, "NOT_FOUND", "API key not found.")
    if body.name is not None:
        record.name = body.name
    if body.permissions is not None:
        record.permissions = _validate_permissions(body.permissions)
    if body.rate_limit is not None:
        record.rate_limit = body.rate_limit
    if body.active is not None:
        record.active = body.active
    db.commit()
    db.refresh(record)
    return {"success": True, "data": _public(record)}


@router.post("/keys/{key_id}/revoke", summary="Revoke key (admin)")
def revoke_key(request: Request, key_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not record:
        raise APIError(404, "NOT_FOUND", "API key not found.")
    record.active = False
    db.commit()
    return {"success": True, "data": _public(record)}


@router.post("/keys/{key_id}/reactivate", summary="Reactivate key (admin)")
def reactivate_key(request: Request, key_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not record:
        raise APIError(404, "NOT_FOUND", "API key not found.")
    record.active = True
    db.commit()
    return {"success": True, "data": _public(record)}


@router.delete("/keys/{key_id}", summary="Delete key (admin)")
def delete_key(request: Request, key_id: int, db: Session = Depends(get_db)):
    require_admin(request)
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not record:
        raise APIError(404, "NOT_FOUND", "API key not found.")
    db.delete(record)
    db.commit()
    return {"success": True, "data": {"deleted": key_id}}


@router.get("/stats", summary="Basic usage (admin)")
def stats(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    total = db.query(func.count(ApiKey.id)).scalar() or 0
    active = db.query(func.count(ApiKey.id)).filter(ApiKey.active.is_(True)).scalar() or 0
    requests = db.query(func.coalesce(func.sum(ApiKey.request_count), 0)).scalar() or 0
    return {"success": True, "data": {"total_keys": total, "active_keys": active, "total_requests": int(requests)}}
