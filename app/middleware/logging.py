"""Structured request logging with secret redaction."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.utils.security import redact_secrets

log = logging.getLogger("media_api.access")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            latency = time.perf_counter() - start
            rid = getattr(request.state, "request_id", "-")
            client = getattr(request.state, "api_key_name", "anon")
            provider = getattr(request.state, "provider", "-")
            status = getattr(response, "status_code", 500) if response is not None else 500
            line = (
                f"request_id={rid} client={client} "
                f"{request.method} {request.url.path} "
                f"provider={provider} status={status} latency={latency:.2f}s"
            )
            log.info(redact_secrets(line))
