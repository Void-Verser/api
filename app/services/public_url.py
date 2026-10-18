"""Determine the real public base URL of this deployment.

Priority: API_BASE_URL (explicit, e.g. a custom domain) -> Vercel
production domain -> Vercel deployment URL -> the incoming request's
origin. Placeholder values like api.YOUR_DOMAIN.com are ignored.
"""

from __future__ import annotations

import os

from fastapi import Request

from app.config import Settings


def _clean(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def public_base_url(settings: Settings, request: Request | None = None) -> tuple[str, str]:
    explicit = _clean(settings.api_base_url or "")
    if explicit and "your_domain" not in explicit.lower():
        return explicit, "API_BASE_URL"
    prod = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "")
    if prod:
        return _clean(prod), "VERCEL_PROJECT_PRODUCTION_URL"
    dep = os.getenv("VERCEL_URL", "")
    if dep:
        return _clean(dep), "VERCEL_URL"
    if request is not None:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        return f"{proto}://{host}".rstrip("/"), "request"
    return "", "unknown"
