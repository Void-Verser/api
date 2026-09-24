"""Client API-key authentication (X-API-Key: mapi_...)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey
from app.schemas.errors import APIError
from app.utils.security import hash_api_key


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def lookup_key(db: Session, presented: str) -> ApiKey | None:
    return db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(presented)).first()


def authenticate_request(request: Request, db: Session) -> ApiKey:
    presented = request.headers.get("x-api-key", "").strip()
    if not presented:
        raise APIError(401, "MISSING_API_KEY", "Missing X-API-Key header.")
    record = lookup_key(db, presented)
    if record is None:
        raise APIError(401, "INVALID_API_KEY", "Invalid API key.")
    if not record.active:
        raise APIError(401, "KEY_REVOKED", "This API key has been revoked or deactivated.")
    if record.is_expired():
        raise APIError(401, "KEY_EXPIRED", "This API key has expired.")
    # Usage tracking (best effort — never fail a request because of it).
    try:
        record.request_count = (record.request_count or 0) + 1
        record.last_used_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
    # Stash for logging / downstream use; never the plaintext key.
    request.state.api_key_id = record.id
    request.state.api_key_name = record.name
    request.state.api_key_record = record
    return record


def require_scope(record: ApiKey, scope: str) -> None:
    if not record.has_permission(scope):
        raise APIError(
            403, "FORBIDDEN_SCOPE", f"This API key lacks the '{scope}' permission."
        )
