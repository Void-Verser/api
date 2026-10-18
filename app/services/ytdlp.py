"""Shared yt-dlp helpers (API stream extraction + worker downloads).

yt-dlp is imported lazily so the API boots without it. It never bypasses
DRM; restricted/private media surfaces as a classified error instead.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from app.config import Settings


def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def find_ffmpeg(settings: Settings) -> str | None:
    if settings.ffmpeg_path and os.path.exists(settings.ffmpeg_path):
        return settings.ffmpeg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # optional bundled binary (pip install imageio-ffmpeg)
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def base_options(settings: Settings, **extra) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": settings.provider_timeout_seconds,
        "retries": 2,
        "cachedir": False,
    }
    if settings.ytdlp_cookies_file:
        opts["cookiefile"] = settings.ytdlp_cookies_file
    if settings.ytdlp_proxy:
        opts["proxy"] = settings.ytdlp_proxy
    opts.update(extra)
    return opts


@dataclass
class ClassifiedError:
    code: str
    message: str
    retryable: bool


_RULES: list[tuple[tuple[str, ...], str, str, bool]] = [
    (("private video",), "MEDIA_PRIVATE", "This video is private.", False),
    (("confirm your age", "age-restricted", "inappropriate for some users"),
     "MEDIA_RESTRICTED", "This media is age-restricted and cannot be retrieved.", False),
    (("not available in your country", "geo restrict", "blocked it in your country"),
     "MEDIA_REGION_BLOCKED", "This media is not available in the server's region.", False),
    (("drm",), "MEDIA_DRM", "This media is DRM-protected and cannot be retrieved.", False),
    (("members-only", "join this channel", "requires payment", "premium"),
     "MEDIA_RESTRICTED", "This media requires a membership or purchase.", False),
    (("not a bot", "sign in to confirm"),
     "PROVIDER_BLOCKED", "The provider is blocking requests from this server (bot check). "
     "Configure YTDLP_COOKIES_FILE or YTDLP_PROXY, or run the worker elsewhere.", False),
    (("is live", "live event", "premieres in"),
     "MEDIA_LIVE", "Live streams and premieres cannot be converted.", False),
    (("video unavailable", "has been removed", "terminated", "does not exist", "404"),
     "MEDIA_UNAVAILABLE", "This media is unavailable or has been removed.", False),
    (("timed out", "timeout", "connection reset", "temporary failure", "503", "502", "500"),
     "PROVIDER_TEMPORARY", "Temporary provider/network failure.", True),
]


def classify_error(exc: BaseException | str) -> ClassifiedError:
    text = str(exc).lower()
    for needles, code, message, retryable in _RULES:
        if any(n in text for n in needles):
            return ClassifiedError(code, message, retryable)
    return ClassifiedError("PROCESSING_FAILED", "Media processing failed.", True)
