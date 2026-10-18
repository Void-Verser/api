"""Spotify — catalog only (search, metadata, artwork, official links).

Uses the Client Credentials flow. Spotify does not permit audio
extraction, so stream/download/convert are intentionally unsupported.
"""

from __future__ import annotations

import base64
import re
import time

import httpx

from app.config import Settings
from app.providers.base import (
    INFO,
    SEARCH,
    MediaProvider,
    ProviderBadResponseError,
    ProviderInvalidQueryError,
    ProviderUnavailableError,
)
from app.providers.http import request_json

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TRACK_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_token_cache: dict[str, tuple[str, float]] = {}


class SpotifyProvider(MediaProvider):
    name = "spotify"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.client_id = (settings.spotify_client_id or "").strip()
        self.client_secret = (settings.spotify_client_secret or "").strip()
        self.market = settings.spotify_market or "US"
        self.timeout = settings.provider_timeout_seconds
        self.transport = transport
        self.capabilities = frozenset({SEARCH, INFO}) if self.enabled else frozenset()

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def disabled_reason(self) -> str:
        return "credentials not configured (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)"

    async def _token(self) -> str:
        cached = _token_cache.get(self.client_id)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        data = await request_json(
            self.name, "POST", TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
            timeout=self.timeout, transport=self.transport,
        )
        token = (data or {}).get("access_token")
        if not token:
            raise ProviderUnavailableError("spotify token request failed")
        _token_cache[self.client_id] = (token, time.time() + int(data.get("expires_in", 3600)))
        return token

    async def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            raise ProviderUnavailableError(f"Provider unavailable: {self.disabled_reason()}")
        token = await self._token()
        data = await request_json(
            self.name, "GET", f"{API}{path}", params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout, transport=self.transport,
        )
        if not isinstance(data, dict):
            raise ProviderBadResponseError("spotify returned malformed data")
        return data

    @staticmethod
    def native_id(media_id: str) -> str:
        tid = media_id.split(":")[-1]
        if not TRACK_ID_RE.match(tid):
            raise ProviderInvalidQueryError("Invalid Spotify track id.")
        return tid

    def _normalize(self, t: dict) -> dict:
        album = t.get("album") or {}
        images = album.get("images") or []
        dur = t.get("duration_ms")
        return {
            "id": f"spotify:{t.get('id')}",
            "title": t.get("name") or "Unknown",
            "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []) or None,
            "album": album.get("name"),
            "duration": int(dur / 1000) if isinstance(dur, (int, float)) else None,
            "thumbnail": images[0].get("url") if images else None,
            "provider": self.name,
            "media_type": "track",
            "url": (t.get("external_urls") or {}).get("spotify"),
            "preview_url": t.get("preview_url"),
            "isrc": (t.get("external_ids") or {}).get("isrc"),
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        data = await self._get(
            "/search", {"q": query, "type": "track", "limit": min(limit, 50),
                        "offset": offset, "market": self.market},
        )
        items = (data.get("tracks") or {}).get("items") or []
        return [self._normalize(t) for t in items if isinstance(t, dict) and t.get("id")]

    async def get_info(self, media_id: str) -> dict:
        data = await self._get(f"/tracks/{self.native_id(media_id)}", {"market": self.market})
        return self._normalize(data)
