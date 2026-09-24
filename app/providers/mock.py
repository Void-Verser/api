"""Deterministic mock provider for dev/test without real credentials.

Always enabled (unless disabled via settings) and returns fake but
well-shaped normalized results. Uses the .invalid TLD so URLs are
obviously non-routable placeholders.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.providers.base import (
    MediaProvider,
    ProviderNotFoundError,
)


def _slug(query: str) -> str:
    h = hashlib.sha1(query.encode()).hexdigest()[:8]
    safe = "".join(c if c.isalnum() else "-" for c in query.strip().lower())[:24].strip("-")
    return f"{safe or 'track'}-{h}"


class MockProvider(MediaProvider):
    name = "mock"

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
                    "id": f"mock-{slug}-{idx}",
                    "title": f"{query.strip()} (Mock Mix {idx})",
                    "artist": "Mock Artist",
                    "album": "Mock Album",
                    "duration": 180 + idx * 7,
                    "thumbnail": "https://cdn.example.invalid/thumb/mock.jpg",
                    "provider": self.name,
                    "media_type": "audio",
                }
            )
        return items

    async def get_info(self, media_id: str) -> dict:
        if not media_id.startswith("mock-") and ":" not in media_id:
            # Accept bare mock ids and provider-prefixed ids; otherwise 404.
            if not media_id.startswith("mock"):
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

    def __init__(self, message: str = "boom"):
        self.message = message

    @property
    def enabled(self) -> bool:
        return True

    async def search(self, query: str, limit: int = 10, offset: int = 0):
        from app.providers.base import ProviderUnavailableError

        raise ProviderUnavailableError(self.message)

    async def get_info(self, media_id: str):
        from app.providers.base import ProviderUnavailableError

        raise ProviderUnavailableError(self.message)

    async def get_stream(self, media_id: str):
        from app.providers.base import ProviderUnavailableError

        raise ProviderUnavailableError(self.message)

    async def get_download(self, media_id: str):
        from app.providers.base import ProviderUnavailableError

        raise ProviderUnavailableError(self.message)
