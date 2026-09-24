"""HTTP-backed provider skeletons (A/B/C).

Each reports "unavailable: credentials not configured" when its env key is
missing instead of crashing the API. When credentials ARE present, the code
attempts a generic upstream call with a timeout; replace `_call_upstream`
with the real provider integration without touching routes.
"""

from __future__ import annotations

import httpx

from app.providers.base import (
    MediaProvider,
    ProviderBadResponseError,
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class HttpProviderBase(MediaProvider):
    name = "http_base"

    def __init__(self, api_key: str = "", base_url: str = "", timeout: float = 10.0):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _require_enabled(self) -> None:
        if not self.api_key or not self.base_url:
            raise ProviderUnavailableError(
                f"Provider unavailable: credentials not configured ({self.name})"
            )

    async def _call_upstream(self, path: str, params: dict | None = None) -> dict:
        self._require_enabled()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params or {}, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.name} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name} unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise ProviderNotFoundError(f"Media not found via {self.name}")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(f"{self.name} upstream error {resp.status_code}")
        if resp.status_code != 200:
            raise ProviderBadResponseError(f"{self.name} unexpected status {resp.status_code}")
        try:
            data = resp.json()
        except Exception as exc:
            raise ProviderBadResponseError(f"{self.name} malformed response") from exc
        if not isinstance(data, dict):
            raise ProviderBadResponseError(f"{self.name} malformed response")
        return data

    # -- Normalization -----------------------------------------------------
    # Upstream shapes differ; subclasses override these hooks. Defaults map
    # a generic {items:[...]} shape and raise BadResponse when unexpected.

    def _normalize_item(self, raw: dict) -> dict:
        try:
            return {
                "id": str(raw.get("id") or raw.get("song_id") or raw.get("name") or "unknown"),
                "title": str(raw.get("title") or raw.get("song_name") or raw.get("name") or "Unknown"),
                "artist": raw.get("artist") or raw.get("artist_name") or raw.get("performer"),
                "album": raw.get("album"),
                "duration": raw.get("duration") or raw.get("duration_seconds") or raw.get("length"),
                "thumbnail": raw.get("thumbnail") or raw.get("artwork"),
                "provider": self.name,
                "media_type": "audio",
            }
        except Exception as exc:
            raise ProviderBadResponseError(f"{self.name} normalize failed") from exc

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        data = await self._call_upstream("/search", {"q": query, "limit": limit, "offset": offset})
        items = data.get("items") or data.get("results") or []
        if not isinstance(items, list):
            raise ProviderBadResponseError(f"{self.name} malformed search response")
        return [self._normalize_item(i) for i in items[:limit]]

    async def get_info(self, media_id: str) -> dict:
        data = await self._call_upstream(f"/info/{media_id}")
        item = data.get("item", data)
        return self._normalize_item(item if isinstance(item, dict) else {})

    async def get_stream(self, media_id: str) -> dict:
        data = await self._call_upstream(f"/stream/{media_id}")
        url = data.get("url")
        if not url:
            raise ProviderBadResponseError(f"{self.name} missing stream url")
        return {
            "id": media_id,
            "url": url,
            "provider": self.name,
            "expires_at": data.get("expires_at"),
            "content_type": data.get("content_type", "audio/mpeg"),
            "title": data.get("title"),
        }

    async def get_download(self, media_id: str) -> dict:
        data = await self._call_upstream(f"/download/{media_id}")
        url = data.get("url")
        if not url:
            raise ProviderBadResponseError(f"{self.name} missing download url")
        return {
            "id": media_id,
            "url": url,
            "provider": self.name,
            "expires_at": data.get("expires_at"),
            "filename": data.get("filename", f"{media_id}.mp3"),
            "content_type": data.get("content_type", "audio/mpeg"),
            "title": data.get("title"),
        }

    def status(self):  # more informative than base
        from app.providers.base import ProviderStatus

        if self.enabled:
            return ProviderStatus(name=self.name, enabled=True)
        return ProviderStatus(
            name=self.name, enabled=False, reason="credentials not configured"
        )
