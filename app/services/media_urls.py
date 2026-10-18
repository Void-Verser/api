"""Offline recognition of supported media URLs -> (provider, kind, id).

Nothing here touches the network except `expand_short_link`, which uses
the SSRF-safe redirect follower for a small allowlist of short-link hosts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from app.utils.netsec import UnsafeURLError, follow_redirects_safely

YT_ID = r"[A-Za-z0-9_-]{11}"
_YT_ID_RE = re.compile(rf"^{YT_ID}$")
YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
SPOTIFY_HOSTS = {"open.spotify.com", "spotify.link"}
APPLE_HOSTS = {"music.apple.com", "geo.music.apple.com"}
SOUNDCLOUD_HOSTS = {"soundcloud.com", "on.soundcloud.com"}
SHORT_LINK_HOSTS = {"spotify.link", "on.soundcloud.com"}
# Hosts a short link may legitimately land on.
SHORT_LINK_TARGETS = SHORT_LINK_HOSTS | {"open.spotify.com", "soundcloud.com", "m.soundcloud.com"}


@dataclass
class ParsedMedia:
    provider: str
    kind: str  # video | short | live | track
    id: str  # normalized "<provider>:<native>" (soundcloud permalinks: url)
    canonical_url: str
    native_id: str | None = None


class UnsupportedURL(ValueError):
    pass


def _host_in(host: str, hosts: set[str]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def parse_media_url(raw: str) -> ParsedMedia:
    raw = (raw or "").strip()
    if not raw:
        raise UnsupportedURL("URL is required.")
    if _YT_ID_RE.match(raw):
        return _yt(raw, "video")
    m = re.match(r"^spotify:track:([A-Za-z0-9]{22})$", raw)
    if m:
        return _spotify(m.group(1))
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UnsupportedURL("Only http(s) media links are supported.")
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    query = parse_qs(parts.query)

    if _host_in(host, YOUTUBE_HOSTS):
        if host.endswith("youtu.be"):
            vid = path.strip("/").split("/")[0]
            if _YT_ID_RE.match(vid):
                return _yt(vid, "video")
        m = re.match(rf"^/(shorts|live|embed|v|e)/({YT_ID})(?:[/?]|$)", path)
        if m:
            kind = {"shorts": "short", "live": "live"}.get(m.group(1), "video")
            return _yt(m.group(2), kind)
        if path.rstrip("/") in ("/watch", ""):
            vid = (query.get("v") or [""])[0]
            if _YT_ID_RE.match(vid):
                return _yt(vid, "video")
        raise UnsupportedURL("Unrecognized YouTube link (playlists/channels are not supported).")

    if _host_in(host, {"open.spotify.com"}):
        m = re.match(r"^(?:/intl-[a-z-]+)?/track/([A-Za-z0-9]{22})", path)
        if m:
            return _spotify(m.group(1))
        raise UnsupportedURL("Only Spotify track links are supported.")

    if _host_in(host, APPLE_HOSTS):
        song = (query.get("i") or [""])[0]
        m = re.match(r"^/[a-z]{2}/song/(?:[^/]+/)?(\d+)", path)
        if m:
            song = m.group(1)
        if song.isdigit():
            return ParsedMedia("apple_music", "track", f"apple_music:{song}",
                               f"https://music.apple.com/song/{song}", song)
        raise UnsupportedURL("Only Apple Music song links (or album links with ?i=) are supported.")

    if host in ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"):
        segs = [s for s in path.split("/") if s]
        if len(segs) == 2 and segs[1] not in ("sets", "tracks", "likes", "reposts", "albums"):
            url = f"https://soundcloud.com/{segs[0]}/{segs[1]}"
            return ParsedMedia("soundcloud", "track", url, url, None)
        raise UnsupportedURL("Only single SoundCloud track links are supported.")

    if host in SHORT_LINK_HOSTS:
        raise UnsupportedURL("short-link")  # caller expands then re-parses
    raise UnsupportedURL("Unsupported media host.")


def is_short_link(raw: str) -> bool:
    host = (urlsplit(raw if "://" in raw else "https://" + raw).hostname or "").lower()
    return host in SHORT_LINK_HOSTS


async def expand_short_link(raw: str, transport=None, resolve_dns: bool = True) -> str:
    url = raw if "://" in raw else "https://" + raw
    try:
        return await follow_redirects_safely(
            url, SHORT_LINK_TARGETS, transport=transport, resolve_dns=resolve_dns
        )
    except UnsafeURLError as exc:
        raise UnsupportedURL(str(exc)) from exc


def _yt(vid: str, kind: str) -> ParsedMedia:
    canonical = (f"https://www.youtube.com/shorts/{vid}" if kind == "short"
                 else f"https://www.youtube.com/watch?v={vid}")
    return ParsedMedia("youtube", kind, f"youtube:{vid}", canonical, vid)


def _spotify(tid: str) -> ParsedMedia:
    return ParsedMedia("spotify", "track", f"spotify:{tid}", f"https://open.spotify.com/track/{tid}", tid)
