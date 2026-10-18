"""Apple Music — catalog only (search, metadata, artwork, official links).

Auth is an Apple Music developer token: either a pre-generated
APPLE_MUSIC_DEVELOPER_TOKEN, or one signed here (ES256) from
APPLE_MUSIC_TEAM_ID / APPLE_MUSIC_KEY_ID / APPLE_MUSIC_PRIVATE_KEY.
Full-track audio is DRM-protected; only Apple's preview URL is exposed,
as metadata (`preview_url`), never as a stream.
"""

from __future__ import annotations

import time

import httpx

from app.config import Settings
from app.providers.base import (
    INFO,
    SEARCH,
    MediaProvider,
    ProviderBadResponseError,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.providers.http import request_json

API = "https://api.music.apple.com/v1"
_token_cache: dict[str, tuple[str, float]] = {}


class AppleMusicProvider(MediaProvider):
    name = "apple_music"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.static_token = (settings.apple_music_developer_token or "").strip()
        self.team_id = (settings.apple_music_team_id or "").strip()
        self.key_id = (settings.apple_music_key_id or "").strip()
        self.private_key = (settings.apple_music_private_key or "").replace("\\n", "\n").strip()
        self.storefront = (settings.apple_music_storefront or "us").lower()
        self.timeout = settings.provider_timeout_seconds
        self.transport = transport
        self.capabilities = frozenset({SEARCH, INFO}) if self.enabled else frozenset()

    @property
    def enabled(self) -> bool:
        return bool(self.static_token or (self.team_id and self.key_id and self.private_key))

    def disabled_reason(self) -> str:
        return ("credentials not configured (APPLE_MUSIC_DEVELOPER_TOKEN or "
                "APPLE_MUSIC_TEAM_ID + APPLE_MUSIC_KEY_ID + APPLE_MUSIC_PRIVATE_KEY)")

    def _token(self) -> str:
        if self.static_token:
            return self.static_token
        cached = _token_cache.get(self.key_id)
        if cached and cached[1] > time.time() + 300:
            return cached[0]
        try:
            import jwt
        except ImportError as exc:
            raise ProviderUnavailableError("PyJWT[crypto] is required to sign Apple Music tokens.") from exc
        now = int(time.time())
        exp = now + 12 * 3600
        try:
            token = jwt.encode(
                {"iss": self.team_id, "iat": now, "exp": exp},
                self.private_key, algorithm="ES256", headers={"kid": self.key_id},
            )
        except Exception as exc:
            raise ProviderUnavailableError("Apple Music private key is invalid.") from exc
        _token_cache[self.key_id] = (token, exp)
        return token

    async def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            raise ProviderUnavailableError(f"Provider unavailable: {self.disabled_reason()}")
        data = await request_json(
            self.name, "GET", f"{API}/catalog/{self.storefront}{path}", params=params or {},
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=self.timeout, transport=self.transport,
        )
        if not isinstance(data, dict):
            raise ProviderBadResponseError("apple_music returned malformed data")
        return data

    @staticmethod
    def native_id(media_id: str) -> str:
        sid = media_id.split(":")[-1]
        if not sid.isdigit():
            raise ProviderInvalidQueryError("Invalid Apple Music song id.")
        return sid

    def _normalize(self, song: dict) -> dict:
        a = song.get("attributes") or {}
        art = (a.get("artwork") or {}).get("url")
        dur = a.get("durationInMillis")
        previews = a.get("previews") or []
        return {
            "id": f"apple_music:{song.get('id')}",
            "title": a.get("name") or "Unknown",
            "artist": a.get("artistName"),
            "album": a.get("albumName"),
            "duration": int(dur / 1000) if isinstance(dur, (int, float)) else None,
            "thumbnail": art.replace("{w}", "600").replace("{h}", "600") if art else None,
            "provider": self.name,
            "media_type": "track",
            "url": a.get("url"),
            "preview_url": previews[0].get("url") if previews else None,
            "isrc": a.get("isrc"),
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        data = await self._get(
            "/search", {"term": query, "types": "songs", "limit": min(limit, 25), "offset": offset}
        )
        songs = ((data.get("results") or {}).get("songs") or {}).get("data") or []
        return [self._normalize(s) for s in songs if isinstance(s, dict)]

    async def get_info(self, media_id: str) -> dict:
        data = await self._get(f"/songs/{self.native_id(media_id)}")
        items = data.get("data") or []
        if not items:
            raise ProviderNotFoundError("Apple Music song not found.")
        return self._normalize(items[0])
