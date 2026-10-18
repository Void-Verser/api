"""YouTube — the primary provider.

* Search/metadata: YouTube Data API v3 when YOUTUBE_API_KEY is set, else
  yt-dlp flat search and the public oEmbed endpoint for metadata.
* Stream/download URL resolution: yt-dlp, only when
  MEDIA_EXTRACTION_ENABLED=true (operator attests they are authorized).
  Resolved googlevideo URLs expire and may be bound to the server's IP;
  bots should prefer POST /v1/convert, which returns a hosted file.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

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
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUnsupportedError,
)
from app.providers.http import as_int, request_json
from app.services import ytdlp

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DATA_API = "https://www.googleapis.com/youtube/v3"
OEMBED = "https://www.youtube.com/oembed"
_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_iso8601_duration(value: str | None) -> int | None:
    m = _ISO_DURATION.match(value or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _best_thumb(thumbs: dict | list | None) -> str | None:
    if isinstance(thumbs, dict):
        for k in ("maxres", "standard", "high", "medium", "default"):
            if thumbs.get(k, {}).get("url"):
                return thumbs[k]["url"]
        return None
    if isinstance(thumbs, list) and thumbs:
        return (thumbs[-1] or {}).get("url")
    return None


class YouTubeProvider(MediaProvider):
    name = "youtube"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.api_key = (settings.youtube_api_key or "").strip()
        self.timeout = settings.provider_timeout_seconds
        self.transport = transport
        caps = {INFO}
        if self._search_backend():
            caps.add(SEARCH)
        if settings.media_extraction_enabled and ytdlp.ytdlp_available():
            caps |= {STREAM, DOWNLOAD, CONVERT}
        self.capabilities = frozenset(caps)

    def _search_backend(self) -> str | None:
        choice = (self.settings.youtube_search_backend or "auto").lower()
        if choice in ("auto", "data_api") and self.api_key:
            return "data_api"
        if choice in ("auto", "ytdlp") and ytdlp.ytdlp_available():
            return "ytdlp"
        return None

    def disabled_reason(self) -> str:
        return ""

    def status(self):
        st = super().status()
        notes = []
        if not self.api_key:
            notes.append("YOUTUBE_API_KEY not set (using yt-dlp search / oEmbed metadata)")
        if not self.supports(STREAM):
            notes.append("stream/download/convert off (MEDIA_EXTRACTION_ENABLED=false or yt-dlp missing)")
        st.reason = "; ".join(notes)
        return st

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def native_id(media_id: str) -> str:
        vid = media_id.split(":", 1)[1] if media_id.lower().startswith("youtube:") else media_id
        if not VIDEO_ID_RE.match(vid):
            raise ProviderInvalidQueryError("Invalid YouTube video id.")
        return vid

    def _normalize(self, vid: str, title, artist, duration, thumbnail) -> dict:
        return {
            "id": f"youtube:{vid}",
            "title": str(title or "Unknown"),
            "artist": artist,
            "album": None,
            "duration": duration,
            "thumbnail": thumbnail,
            "provider": self.name,
            "media_type": "video",
            "url": watch_url(vid),
        }

    async def _data_api(self, path: str, params: dict) -> dict:
        data = await request_json(
            self.name, "GET", f"{DATA_API}/{path}",
            params={**params, "key": self.api_key}, timeout=self.timeout, transport=self.transport,
        )
        if not isinstance(data, dict):
            raise ProviderBadResponseError("youtube returned malformed data")
        return data

    async def _videos(self, ids: list[str]) -> list[dict]:
        data = await self._data_api(
            "videos", {"part": "snippet,contentDetails,status", "id": ",".join(ids), "maxResults": 50}
        )
        out = []
        for item in data.get("items") or []:
            sn = item.get("snippet") or {}
            out.append(self._normalize(
                item.get("id"), sn.get("title"), sn.get("channelTitle"),
                parse_iso8601_duration((item.get("contentDetails") or {}).get("duration")),
                _best_thumb(sn.get("thumbnails")),
            ))
        return out

    async def _run_ytdlp(self, url: str, **opts) -> dict:
        try:
            import yt_dlp
        except ImportError as exc:
            raise ProviderUnavailableError("yt-dlp is not installed.") from exc

        def _extract():
            with yt_dlp.YoutubeDL(ytdlp.base_options(self.settings, **opts)) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=self.timeout * 3)
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError("youtube extraction timed out") from exc
        except Exception as exc:  # yt_dlp.utils.DownloadError and friends
            c = ytdlp.classify_error(exc)
            if c.code == "MEDIA_UNAVAILABLE":
                raise ProviderNotFoundError(c.message) from exc
            if c.code.startswith("MEDIA_"):
                raise MediaRestrictedError(c.message) from exc
            raise ProviderUnavailableError(c.message) from exc
        if not isinstance(info, dict):
            raise ProviderBadResponseError("youtube extraction returned nothing")
        return info

    # -- operations ---------------------------------------------------------
    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        backend = self._search_backend()
        if backend == "data_api":
            want = min(offset + limit, 50)
            data = await self._data_api(
                "search", {"part": "id", "type": "video", "q": query, "maxResults": want,
                           "safeSearch": "none"},
            )
            ids = [((i.get("id") or {}).get("videoId")) for i in data.get("items") or []]
            ids = [i for i in ids if i][offset:offset + limit]
            if not ids:
                return []
            by_id = {r["id"]: r for r in await self._videos(ids)}
            return [by_id[f"youtube:{i}"] for i in ids if f"youtube:{i}" in by_id]
        if backend == "ytdlp":
            info = await self._run_ytdlp(
                f"ytsearch{min(offset + limit, 50)}:{query}", extract_flat="in_playlist"
            )
            out = []
            for e in (info.get("entries") or [])[offset:offset + limit]:
                vid = (e or {}).get("id")
                if not vid or not VIDEO_ID_RE.match(vid):
                    continue
                out.append(self._normalize(
                    vid, e.get("title"), e.get("channel") or e.get("uploader"),
                    as_int(e.get("duration")), _best_thumb(e.get("thumbnails")),
                ))
            return out
        raise ProviderUnsupportedError(
            "YouTube search needs YOUTUBE_API_KEY or yt-dlp installed."
        )

    async def get_info(self, media_id: str) -> dict:
        vid = self.native_id(media_id)
        if self.api_key:
            items = await self._videos([vid])
            if not items:
                raise ProviderNotFoundError("YouTube video not found, private, or removed.")
            return items[0]
        # Keyless public metadata (fast; no duration — the worker still
        # enforces JOB_MAX_MEDIA_DURATION_SECONDS on conversion).
        try:
            data = await request_json(
                self.name, "GET", OEMBED, params={"url": watch_url(vid), "format": "json"},
                timeout=self.timeout, transport=self.transport,
            )
        except ProviderInvalidQueryError as exc:  # oEmbed answers 400 for bad ids
            raise ProviderNotFoundError("YouTube video not found.") from exc
        except ProviderUnavailableError as exc:
            if "401" in str(exc) or "403" in str(exc):
                raise MediaRestrictedError("This video is private or embedding is disabled.") from exc
            raise
        return self._normalize(
            vid, data.get("title"), data.get("author_name"), None, data.get("thumbnail_url")
        )

    async def _resolve_direct(self, media_id: str, fmt: str) -> dict:
        if not self.supports(STREAM):
            raise ProviderUnsupportedError(
                "YouTube stream/download resolution is disabled on this server "
                "(MEDIA_EXTRACTION_ENABLED=false). Use metadata or ask the operator."
            )
        vid = self.native_id(media_id)
        info = await self._run_ytdlp(watch_url(vid), format=fmt)
        url = info.get("url")
        if not url:
            raise ProviderBadResponseError("youtube returned no playable format")
        expires = None
        exp = parse_qs(urlparse(url).query).get("expire", [None])[0]
        if exp and exp.isdigit():
            expires = datetime.fromtimestamp(int(exp), tz=timezone.utc).isoformat()
        ext = info.get("ext") or "m4a"
        ctype = {"m4a": "audio/mp4", "webm": "audio/webm", "mp4": "video/mp4", "mp3": "audio/mpeg"}.get(
            ext, "application/octet-stream"
        )
        return {
            "id": f"youtube:{vid}",
            "url": url,
            "provider": self.name,
            "expires_at": expires,
            "content_type": ctype,
            "title": info.get("title"),
            "filename": f"{vid}.{ext}",
            "note": "Short-lived upstream URL; may only work from this server's network. "
                    "Use POST /v1/convert for a hosted file.",
        }

    async def get_stream(self, media_id: str) -> dict:
        return await self._resolve_direct(media_id, "bestaudio[ext=m4a]/bestaudio/best")

    async def get_download(self, media_id: str) -> dict:
        return await self._resolve_direct(media_id, "bestaudio[ext=m4a]/bestaudio/best")
