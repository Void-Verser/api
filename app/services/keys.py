"""Client API-key lifecycle shared by the dashboard and /admin automation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.api_key import ALL_SCOPES, DEFAULT_PERMISSIONS, VALID_PERMISSIONS, ApiKey
from app.schemas.errors import APIError
from app.utils.security import generate_api_key, hash_api_key


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def public_key(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "masked_key": key.masked,
        "active": key.active,
        "status": "revoked" if not key.active else ("expired" if key.is_expired() else "active"),
        "permissions": key.permissions or [],
        "all_permissions": ALL_SCOPES in (key.permissions or []),
        "rate_limit": key.rate_limit,
        "request_count": key.request_count or 0,
        "created_at": _iso(key.created_at),
        "expires_at": _iso(key.expires_at),
        "last_used_at": _iso(key.last_used_at),
    }


def validate_permissions(perms: list[str] | None) -> list[str]:
    if perms is None:
        return list(DEFAULT_PERMISSIONS)
    cleaned = sorted({p.strip().lower() for p in perms if p and p.strip()})
    unknown = [p for p in cleaned if p not in VALID_PERMISSIONS]
    if unknown:
        raise APIError(400, "BAD_REQUEST", f"Unknown permissions: {', '.join(unknown)}")
    if not cleaned:
        raise APIError(400, "BAD_REQUEST", "At least one permission is required.")
    return [ALL_SCOPES] if ALL_SCOPES in cleaned else cleaned


def create_key(
    db: Session,
    *,
    name: str,
    owner_id: int | None = None,
    permissions: list[str] | None = None,
    rate_limit: int | None = None,
    expires_in_days: int | None = None,
) -> tuple[ApiKey, str]:
    """Returns (record, plaintext). The plaintext is never stored."""
    plaintext = generate_api_key()
    record = ApiKey(
        name=name.strip() or "Unnamed key",
        owner_id=owner_id,
        key_hash=hash_api_key(plaintext),
        key_last3=plaintext[-3:],
        active=True,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=expires_in_days)) if expires_in_days else None,
        permissions=validate_permissions(permissions),
        rate_limit=rate_limit,
        request_count=0,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, plaintext


def rotate_key(db: Session, record: ApiKey) -> tuple[ApiKey, str]:
    """Revoke `record` and issue a replacement with the same settings."""
    record.active = False
    db.commit()
    return create_key(
        db, name=record.name, owner_id=record.owner_id,
        permissions=record.permissions or None, rate_limit=record.rate_limit,
    )
