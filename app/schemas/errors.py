"""Consistent API error envelope + typed exception."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail


class APIError(Exception):
    """Raise inside routes/deps to produce a structured JSON error."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_payload(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message, "request_id": request_id}}
