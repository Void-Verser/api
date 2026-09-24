"""Shared FastAPI dependencies: auth + rate limit + resolver wiring."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth import api_keys as client_auth
from app.config import Settings, get_settings
from app.database import get_db
from app.models.api_key import ApiKey
from app.providers.manager import ProviderManager
from app.schemas.errors import APIError
from app.services.cache import CacheService, get_cache
from app.services.rate_limit import RateLimiter, get_rate_limiter
from app.services.resolver import MediaResolver


def get_current_api_key(
    request: Request, db: Session = Depends(get_db)
) -> ApiKey:
    return client_auth.authenticate_request(request, db)


def require_permission(scope: str):
    """Dependency factory enforcing one permission scope + per-key rate limit."""

    async def _dep(
        request: Request,
        settings: Settings = Depends(get_settings),
        db: Session = Depends(get_db),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> ApiKey:
        record = client_auth.authenticate_request(request, db)
        client_auth.require_scope(record, scope)
        limit = record.rate_limit or settings.rate_limit_per_minute
        allowed, remaining, reset = await limiter.is_allowed(
            f"key:{record.key_hash[:16]}", limit
        )
        # Informative headers even on success.
        request.state.ratelimit_limit = limit
        request.state.ratelimit_remaining = remaining
        if not allowed:
            raise APIError(
                429, "RATE_LIMITED", f"Rate limit exceeded. Retry in {reset}s."
            )
        return record

    return _dep


def get_manager(settings: Settings = Depends(get_settings)) -> ProviderManager:
    return ProviderManager(settings)


def get_resolver(
    settings: Settings = Depends(get_settings),
    manager: ProviderManager = Depends(get_manager),
    cache: CacheService = Depends(get_cache),
) -> MediaResolver:
    return MediaResolver(manager, cache, settings)
