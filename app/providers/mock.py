"""Deterministic mock provider for dev/test without real credentials.

Only registered when MOCK_PROVIDER_ENABLED=true and APP_ENV is not
production. Every result is labelled `"mock": true` and uses the .invalid
TLD so URLs are obviously non-routable placeholders.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.providers.base import (
    DOWNLOAD,
    INFO,
    SEARCH,
    STREAM,
    MediaProvider,
    ProviderNotFoundError,
    ProviderUnavailableError,
)


def _slug(query: str) -> str:
    h = hashlib.sha1(query.encode()).hexdigest()[:8]
    safe = "".join(c if c.isalnum() else "-" for c in query.strip().lower())[:24].strip("-")
    return f"{safe or 'track'}-{h}"


class MockProvider(MediaProvider):
    name = "mock"
    capabilities = frozenset({SEARCH, INFO, STREAM, DOWNLOAD})

    def __init__(self, enabled: bool = True):
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        slug = _slug(query)
        items = []
        for i in range(offset, offset + limit):
            idx = i + 1
            items.append(
                {
                    "id": f"mock:{slug}-{idx}",
                    "title": f"{query.strip()} (Mock Mix {idx})",
                    "artist": "Mock Artist",
                    "album": "Mock Album",
                    "duration": 180 + idx * 7,
                    "thumbnail": "https://cdn.example.invalid/thumb/mock.jpg",
                    "provider": self.name,
                    "media_type": "audio",
                    "mock": True,
                }
            )
        return items

    async def get_info(self, media_id: str) -> dict:
        if not media_id.startswith(("mock:", "mock-")):
            raise ProviderNotFoundError(f"Media not found: {media_id}")
        clean = media_id.split(":", 1)[-1]
        return {
            "id": media_id,
            "title": f"Mock track {clean}",
            "artist": "Mock Artist",
            "album": "Mock Album",
            "duration": 214,
            "thumbnail": "https://cdn.example.invalid/thumb/mock.jpg",
            "provider": self.name,
            "media_type": "audio",
            "mock": True,
        }

    def _temp_url(self, kind: str, media_id: str) -> dict:
        exp = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        return {
            "id": media_id,
            "url": f"https://cdn.example.invalid/{kind}/{media_id}?expires=900&sig=mock",
            "provider": self.name,
            "expires_at": exp,
            "content_type": "audio/mpeg",
            "title": f"Mock track {media_id}",
            "mock": True,
        }

    async def get_stream(self, media_id: str) -> dict:
        # Resolve info first so unknown ids 404 consistently.
        await self.get_info(media_id)
        return self._temp_url("stream", media_id)

    async def get_download(self, media_id: str) -> dict:
        await self.get_info(media_id)
        d = self._temp_url("download", media_id)
        d["filename"] = f"{media_id}.mp3"
        return d


class FailingProvider(MediaProvider):
    """Test helper: always raises a recoverable error to exercise fallback."""

    name = "failing"
    capabilities = frozenset({SEARCH, INFO, STREAM, DOWNLOAD})

    def __init__(self, message: str = "boom"):
        self.message = message

    async def search(self, query: str, limit: int = 10, offset: int = 0):
        raise ProviderUnavailableError(self.message)

    async def get_info(self, media_id: str):
        raise ProviderUnavailableError(self.message)

    async def get_stream(self, media_id: str):
        raise ProviderUnavailableError(self.message)

    async def get_download(self, media_id: str):
        raise ProviderUnavailableError(self.message)
