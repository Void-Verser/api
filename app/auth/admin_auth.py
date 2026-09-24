"""Separate admin authentication (X-Admin-Key) for /admin/* endpoints.

Never accepts ordinary MEDIA_API_KEY values.
"""

from __future__ import annotations

from fastapi import Request

from app.config import get_settings
from app.schemas.errors import APIError
from app.utils.security import constant_time_compare


def require_admin(request: Request) -> None:
    settings = get_settings()
    secret = (settings.master_api_secret or "").strip()
    if not secret:
        raise APIError(503, "ADMIN_DISABLED", "Admin API is disabled (no secret configured).")
    presented = request.headers.get("x-admin-key", "")
    if not constant_time_compare(presented, secret):
        raise APIError(401, "ADMIN_UNAUTHORIZED", "Invalid admin credentials.")
