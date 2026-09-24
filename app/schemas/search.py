"""Search response schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.media import MediaResult


class SearchDataEnvelope(BaseModel):
    query: str
    results: list[MediaResult]
    provider: str | None = None
