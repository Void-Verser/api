"""Owner (dashboard) authentication: DB-backed sessions + CSRF.

* No sign-up endpoint exists. The owner is bootstrapped from OWNER_EMAIL /
  OWNER_PASSWORD on startup (only if no owner exists yet) or via
  scripts/create_owner.py.
* The session cookie is HttpOnly + SameSite=Strict (+ Secure over HTTPS) and
  holds a random token; only its SHA-256 hash is stored.
* Every state-changing request needs the per-session CSRF token in the
  X-CSRF-Token header and, if the browser sends Origin, a same-origin Origin.
* Client API keys never authenticate here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models.owner import Owner, OwnerSession
from app.schemas.errors import APIError
from app.utils.security import constant_time_compare, generate_token, hash_password, hash_token

log = logging.getLogger("media_api.owner")
COOKIE = "mapi_session"
MIN_PASSWORD_LENGTH = 12


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_bootstrap_owner(db: Session, settings: Settings) -> None:
    """Create the first owner from env. Never overwrites an existing owner."""
    if db.scalar(select(Owner.id).limit(1)) is not None:
        return
    email = (settings.owner_email or "").strip().lower()
    password = settings.owner_password or ""
    if not email or not password:
        log.warning("No dashboard owner exists. Set OWNER_EMAIL and OWNER_PASSWORD "
                    "or run scripts/create_owner.py.")
        return
    if len(password) < MIN_PASSWORD_LENGTH:
        log.error("OWNER_PASSWORD must be at least %s characters; owner not created.",
                  MIN_PASSWORD_LENGTH)
        return
    db.add(Owner(email=email, password_hash=hash_password(password)))
    db.commit()
    log.info("Dashboard owner bootstrapped.")


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def start_session(db: Session, owner: Owner, response: Response, request: Request,
                  settings: Settings) -> OwnerSession:
    token = generate_token()
    ttl = timedelta(hours=settings.session_ttl_hours)
    sess = OwnerSession(token_hash=hash_token(token), csrf_token=generate_token(24),
                        owner_id=owner.id, expires_at=_utcnow() + ttl)
    db.add(sess)
    owner.last_login_at = _utcnow()
    # Opportunistic cleanup of expired sessions.
    db.execute(delete(OwnerSession).where(OwnerSession.expires_at < _utcnow()))
    db.commit()
    response.set_cookie(
        COOKIE, token, max_age=int(ttl.total_seconds()), httponly=True,
        secure=is_https(request) or settings.is_production, samesite="strict", path="/",
    )
    return sess


def end_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(OwnerSession).where(OwnerSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


def current_session(request: Request, db: Session) -> tuple[OwnerSession, Owner] | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    sess = db.scalar(select(OwnerSession).where(OwnerSession.token_hash == hash_token(token)))
    if sess is None or _aware(sess.expires_at) <= _utcnow():
        return None
    owner = db.get(Owner, sess.owner_id)
    return (sess, owner) if owner else None


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser client; CSRF token still required
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return urlsplit(origin).netloc == host


def require_owner(request: Request, db: Session = Depends(get_db)) -> tuple[OwnerSession, Owner]:
    found = current_session(request, db)
    if found is None:
        raise APIError(401, "OWNER_UNAUTHORIZED", "Sign in to the dashboard first.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not _same_origin(request):
            raise APIError(403, "CSRF_FAILED", "Cross-origin request rejected.")
        if not constant_time_compare(request.headers.get("x-csrf-token", ""), found[0].csrf_token):
            raise APIError(403, "CSRF_FAILED", "Missing or invalid CSRF token.")
    return found

