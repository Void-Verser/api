"""Request-ID middleware: every response carries X-Request-ID."""

from __future__ import annotations

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_ctx.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", "").strip() or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        request_id_ctx.set(rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
