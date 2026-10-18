"""SoundCloud — search, metadata, stream and (uploader-enabled) downloads.

Official API with OAuth 2.1 client credentials. Streams are only returned
for tracks whose `access` is "playable"; downloads only when the uploader
enabled them. Conversion of public tracks uses yt-dlp when
MEDIA_EXTRACTION_ENABLED=true (DRM/Go+ content is not retrievable).
"""

from __future__ import annotations

import base64
import time

import httpx

from app.config import Settings
from app.providers.base import (
    CONVERT,
    DOWNLOAD,
    INFO,
    SEARCH,
    STREAM,
    MediaProvider,
    MediaRestrictedError,
    ProviderBadResponseError,
    ProviderInvalidQueryError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.http import request_json
from app.services import ytdlp

API = "https://api.soundcloud.com"
TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
_token_cache: dict[str, tuple[str, float]] = {}


class SoundCloudProvider(MediaProvider):
    name = "soundcloud"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.client_id = (settings.soundcloud_client_id or "").strip()
        self.client_secret = (settings.soundcloud_client_secret or "").strip()
        self.timeout = settings.provider_timeout_seconds
        self.transport = transport
        caps: set[str] = set()
        if self.client_id and self.client_secret:
            caps |= {SEARCH, INFO, STREAM, DOWNLOAD}
        if settings.media_extraction_enabled and ytdlp.ytdlp_available():
            caps.add(CONVERT)
        self.capabilities = frozenset(caps)

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def enabled(self) -> bool:
        return bool(self.capabilities)

    def disabled_reason(self) -> str:
        return "credentials not configured (SOUNDCLOUD_CLIENT_ID / SOUNDCLOUD_CLIENT_SECRET)"

    async def _token(self) -> str:
        cached = _token_cache.get(self.client_id)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        data = await request_json(
            self.name, "POST", TOKEN_URL, data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}", "Accept": "application/json; charset=utf-8"},
            timeout=self.timeout, transport=self.transport,
        )
        token = (data or {}).get("access_token")
        if not token:
            raise ProviderUnavailableError("soundcloud token request failed")
        _token_cache[self.client_id] = (token, time.time() + int(data.get("expires_in", 3600)))
        return token

    async def _headers(self) -> dict:
        if not self.has_credentials:
            raise ProviderUnavailableError(f"Provider unavailable: {self.disabled_reason()}")
        return {"Authorization": f"OAuth {await self._token()}", "Accept": "application/json; charset=utf-8"}

    async def _get(self, path: str, params: dict | None = None):
        return await request_json(
            self.name, "GET", f"{API}{path}", params=params or {}, headers=await self._headers(),
            timeout=self.timeout, transport=self.transport,
        )

    async def _redirect_target(self, url: str) -> str:
        """SoundCloud stream/download endpoints 302 to a signed CDN URL."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.get(url, headers=await self._headers(), follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("soundcloud timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("soundcloud unreachable") from exc
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            return resp.headers["location"]
        if resp.status_code in (401, 403):
            raise MediaRestrictedError("SoundCloud refused access to this track.")
        if resp.status_code == 200:
            try:
                loc = resp.json().get("location") or resp.json().get("redirectUri")
            except Exception:
                loc = None
            if loc:
                return loc
        raise ProviderBadResponseError(f"soundcloud unexpected status {resp.status_code}")

    @staticmethod
    def native_id(media_id: str) -> str:
        tid = media_id.split(":")[-1]
        if not tid.isdigit():
            raise ProviderInvalidQueryError("Invalid SoundCloud track id.")
        return tid

    def _normalize(self, t: dict) -> dict:
        dur = t.get("duration")
        art = t.get("artwork_url") or (t.get("user") or {}).get("avatar_url")
        return {
            "id": f"soundcloud:{t.get('id')}",
            "title": t.get("title") or "Unknown",
            "artist": (t.get("user") or {}).get("username"),
            "album": None,
            "duration": int(dur / 1000) if isinstance(dur, (int, float)) else None,
            "thumbnail": art.replace("-large.", "-t500x500.") if isinstance(art, str) else None,
            "provider": self.name,
            "media_type": "track",
            "url": t.get("permalink_url"),
            "access": t.get("access"),
            "downloadable": bool(t.get("downloadable")),
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        if not self.has_credentials:
            raise self._unsupported("search without API credentials")
        data = await self._get("/tracks", {"q": query, "limit": min(limit, 50), "offset": offset,
                                           "linked_partitioning": "true"})
        items = data.get("collection") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ProviderBadResponseError("soundcloud malformed search response")
        return [self._normalize(t) for t in items if isinstance(t, dict) and t.get("id")]

    async def _track(self, media_id: str) -> dict:
        data = await self._get(f"/tracks/{self.native_id(media_id)}")
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderBadResponseError("soundcloud malformed track")
        return data

    async def get_info(self, media_id: str) -> dict:
        if not self.has_credentials:
            raise self._unsupported("metadata without API credentials")
        return self._normalize(await self._track(media_id))

    async def resolve_permalink(self, url: str) -> dict:
        data = await self._get("/resolve", {"url": url})
        if not isinstance(data, dict) or data.get("kind") != "track":
            raise ProviderInvalidQueryError("That SoundCloud link is not a single track.")
        return self._normalize(data)

    async def get_stream(self, media_id: str) -> dict:
        if not self.has_credentials:
            raise self._unsupported("stream resolution without API credentials")
        track = await self._track(media_id)
        if track.get("access") not in (None, "playable"):
            raise MediaRestrictedError("This SoundCloud track is preview-only or blocked for streaming.")
        streams = await self._get(f"/tracks/{track['id']}/streams")
        src = (streams or {}).get("http_mp3_128_url") if isinstance(streams, dict) else None
        if not src:
            raise MediaRestrictedError("No progressive stream is available for this track.")
        return {
            "id": f"soundcloud:{track['id']}",
            "url": await self._redirect_target(src),
            "provider": self.name,
            "expires_at": None,
            "content_type": "audio/mpeg",
            "title": track.get("title"),
        }

    async def get_download(self, media_id: str) -> dict:
        if not self.has_credentials:
            raise self._unsupported("download resolution without API credentials")
        track = await self._track(media_id)
        if not track.get("downloadable"):
            raise MediaRestrictedError("The uploader has not enabled downloads for this track.")
        return {
            "id": f"soundcloud:{track['id']}",
            "url": await self._redirect_target(f"{API}/tracks/{track['id']}/download"),
            "provider": self.name,
            "expires_at": None,
            "filename": f"soundcloud-{track['id']}",
            "content_type": None,
            "title": track.get("title"),
        }
