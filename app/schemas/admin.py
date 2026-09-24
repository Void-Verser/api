"""Admin schemas for API-key management."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    permissions: list[str] = Field(default_factory=lambda: ["search", "info"])
    rate_limit: Optional[int] = Field(default=None, ge=1, le=10000)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class UpdateKeyRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    permissions: Optional[list[str]] = None
    rate_limit: Optional[int] = Field(default=None, ge=1, le=10000)
    active: Optional[bool] = None


class KeyPublic(BaseModel):
    id: int
    name: str
    masked_key: str
    active: bool
    permissions: list[str]
    rate_limit: Optional[int]
    request_count: int
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None


class CreateKeyResponse(BaseModel):
    success: bool = True
    data: dict
