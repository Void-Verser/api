"""Normalized media schemas — stable public contract for all clients."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MediaResult(BaseModel):
    id: str
    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    duration: Optional[int] = None  # seconds
    thumbnail: Optional[str] = None
    provider: str
    media_type: str = "audio"


class MediaInfo(MediaResult):
    pass


class StreamData(BaseModel):
    id: str
    url: str
    provider: str
    expires_at: Optional[str] = None
    content_type: Optional[str] = None
    title: Optional[str] = None


class DownloadData(BaseModel):
    id: str
    url: str
    provider: str
    expires_at: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    title: Optional[str] = None


class SearchData(BaseModel):
    query: str
    results: list[MediaResult] = Field(default_factory=list)
    provider: Optional[str] = None  # provider that served the response


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}
