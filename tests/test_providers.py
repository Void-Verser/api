"""Provider adapters against mocked upstream HTTP (no live network)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.providers import apple_music, soundcloud, spotify
from app.providers.apple_music import AppleMusicProvider
from app.providers.base import (
    CONVERT,
    SEARCH,
    STREAM,
    MediaRestrictedError,
    ProviderBadResponseError,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUnsupportedError,
)
from app.providers.manager import ProviderManager
from app.providers.mock import FailingProvider, MockProvider
from app.providers.soundcloud import SoundCloudProvider
from app.providers.spotify import SpotifyProvider
from app.providers.youtube import YouTubeProvider, parse_iso8601_duration


def _settings(**over) -> Settings:
    base = dict(_env_file=None, provider_timeout_seconds=1.0, youtube_search_backend="off",
                mock_provider_enabled=False, app_env="test")
    base.update(over)
    return Settings(**base)


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clear_token_caches():
    for mod in (spotify, soundcloud, apple_music):
        mod._token_cache.clear()


# --- YouTube (primary) ----------------------------------------------------------
def test_iso_duration():
    assert parse_iso8601_duration("PT3M33S") == 213
    assert parse_iso8601_duration("PT1H2M") == 3720
    assert parse_iso8601_duration("P1DT1S") == 86401
    assert parse_iso8601_duration("") is None


@pytest.mark.asyncio
async def test_youtube_data_api_search_normalizes_and_keeps_order():
    def handler(req: httpx.Request):
        assert req.url.params["key"] == "yt-test-key"
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [
                {"id": {"videoId": "dQw4w9WgXcQ"}}, {"id": {"videoId": "9bZkp7q19f0"}}]})
        assert req.url.params["id"] == "dQw4w9WgXcQ,9bZkp7q19f0"
        return httpx.Response(200, json={"items": [
            {"id": "9bZkp7q19f0", "snippet": {"title": "Gangnam Style", "channelTitle": "officialpsy",
                                               "thumbnails": {"high": {"url": "https://i.ytimg.com/b.jpg"}}},
             "contentDetails": {"duration": "PT4M13S"}},
            {"id": "dQw4w9WgXcQ", "snippet": {"title": "Never Gonna Give You Up", "channelTitle": "Rick Astley",
                                               "thumbnails": {"default": {"url": "https://i.ytimg.com/a.jpg"}}},
             "contentDetails": {"duration": "PT3M33S"}},
        ]})

    p = YouTubeProvider(_settings(youtube_api_key="yt-test-key", youtube_search_backend="auto"),
                        transport=_transport(handler))
    assert p.supports(SEARCH)
    results = await p.search("rick", limit=2)
    assert [r["id"] for r in results] == ["youtube:dQw4w9WgXcQ", "youtube:9bZkp7q19f0"]
    assert results[0]["duration"] == 213
    assert results[0]["artist"] == "Rick Astley"
    assert results[0]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert results[0]["provider"] == "youtube"


@pytest.mark.asyncio
async def test_youtube_data_api_missing_video_is_not_found():
    p = YouTubeProvider(_settings(youtube_api_key="k"),
                        transport=_transport(lambda r: httpx.Response(200, json={"items": []})))
    with pytest.raises(ProviderNotFoundError):
        await p.get_info("youtube:dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_youtube_oembed_metadata_without_key():
    def handler(req):
        assert req.url.host == "www.youtube.com" and req.url.path == "/oembed"
        return httpx.Response(200, json={"title": "Song", "author_name": "Artist",
                                         "thumbnail_url": "https://i.ytimg.com/x.jpg"})

    p = YouTubeProvider(_settings(), transport=_transport(handler))
    info = await p.get_info("dQw4w9WgXcQ")
    assert info["id"] == "youtube:dQw4w9WgXcQ" and info["title"] == "Song" and info["duration"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(401, MediaRestrictedError), (404, ProviderNotFoundError),
                                        (400, ProviderNotFoundError), (503, ProviderUnavailableError)])
async def test_youtube_oembed_error_mapping(status, exc):
    p = YouTubeProvider(_settings(), transport=_transport(lambda r: httpx.Response(status, json={})))
    with pytest.raises(exc):
        await p.get_info("dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_youtube_timeout_and_malformed():
    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderTimeoutError):
        await YouTubeProvider(_settings(youtube_api_key="k"), transport=_transport(boom)).get_info("dQw4w9WgXcQ")
    bad = _transport(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(ProviderBadResponseError):
        await YouTubeProvider(_settings(youtube_api_key="k"), transport=bad).get_info("dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_youtube_invalid_id_and_disabled_extraction():
    p = YouTubeProvider(_settings())
    with pytest.raises(ProviderInvalidQueryError):
        await p.get_info("not-a-video-id")
    assert not p.supports(STREAM) and not p.supports(CONVERT)
    with pytest.raises(ProviderUnsupportedError, match="MEDIA_EXTRACTION_ENABLED"):
        await p.get_stream("dQw4w9WgXcQ")


def test_youtube_search_backend_off_has_no_search():
    assert not YouTubeProvider(_settings(youtube_search_backend="off")).supports(SEARCH)


# --- Spotify (catalog only) -------------------------------------------------------
@pytest.mark.asyncio
async def test_spotify_unconfigured_reports_cleanly():
    p = SpotifyProvider(_settings())
    assert p.enabled is False and p.capabilities == frozenset()
    assert "SPOTIFY_CLIENT_ID" in p.status().reason


@pytest.mark.asyncio
async def test_spotify_search_with_cached_token_and_no_audio():
    calls = {"token": 0}

    def handler(req):
        if req.url.host == "accounts.spotify.com":
            calls["token"] += 1
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"tracks": {"items": [{
            "id": "4cOdK2wGLETKBW3PvgPWqT", "name": "Never Gonna Give You Up", "duration_ms": 213573,
            "artists": [{"name": "Rick Astley"}], "album": {"name": "Whenever", "images": [{"url": "https://i.scdn.co/a"}]},
            "external_urls": {"spotify": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"},
            "external_ids": {"isrc": "GBARL9300135"}}]}})

    p = SpotifyProvider(_settings(spotify_client_id="id", spotify_client_secret="s"), transport=_transport(handler))
    r1 = await p.search("rick")
    await p.search("rick again")
    assert calls["token"] == 1
    assert r1[0]["id"] == "spotify:4cOdK2wGLETKBW3PvgPWqT"
    assert r1[0]["duration"] == 213 and r1[0]["isrc"] == "GBARL9300135"
    assert not p.supports(STREAM)
    with pytest.raises(ProviderUnsupportedError):
        await p.get_stream("spotify:4cOdK2wGLETKBW3PvgPWqT")


# --- Apple Music (catalog only) ----------------------------------------------------
@pytest.mark.asyncio
async def test_apple_music_search_and_not_found():
    def handler(req):
        assert req.headers["authorization"] == "Bearer dev-token"
        if "/search" in req.url.path:
            return httpx.Response(200, json={"results": {"songs": {"data": [{
                "id": "1558533900", "attributes": {
                    "name": "Song", "artistName": "Artist", "albumName": "Album", "durationInMillis": 200000,
                    "artwork": {"url": "https://is1.mzstatic.com/{w}x{h}bb.jpg"},
                    "url": "https://music.apple.com/us/song/1558533900",
                    "previews": [{"url": "https://audio-ssl.itunes.apple.com/p.m4a"}]}}]}}})
        return httpx.Response(200, json={"data": []})

    p = AppleMusicProvider(_settings(apple_music_developer_token="dev-token"), transport=_transport(handler))
    res = await p.search("song")
    assert res[0]["id"] == "apple_music:1558533900"
    assert res[0]["thumbnail"] == "https://is1.mzstatic.com/600x600bb.jpg"
    assert res[0]["preview_url"].endswith("p.m4a")  # metadata only, never a stream
    assert not p.supports(STREAM)
    with pytest.raises(ProviderNotFoundError):
        await p.get_info("apple_music:123")


def test_apple_music_signs_es256_developer_token():
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    p = AppleMusicProvider(_settings(apple_music_team_id="TEAM123", apple_music_key_id="KEY123",
                                     apple_music_private_key=pem.replace("\n", "\\n")))
    token = p._token()
    assert jwt.get_unverified_header(token)["kid"] == "KEY123"
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"])
    assert claims["iss"] == "TEAM123"


# --- SoundCloud ----------------------------------------------------------------------
def _sc_handler(track: dict):
    def handler(req):
        if req.url.host == "secure.soundcloud.com":
            return httpx.Response(200, json={"access_token": "sc", "expires_in": 3600})
        assert req.headers["authorization"] == "OAuth sc"
        path = req.url.path
        if path == "/tracks":
            return httpx.Response(200, json={"collection": [track]})
        if path.endswith("/streams"):
            return httpx.Response(200, json={"http_mp3_128_url": "https://api.soundcloud.com/stream/x"})
        if path == "/stream/x":
            return httpx.Response(302, headers={"location": "https://cf-media.sndcdn.com/x.mp3?sig"})
        if path.startswith("/tracks/"):
            return httpx.Response(200, json=track)
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_soundcloud_search_and_stream():
    track = {"id": 123, "title": "T", "duration": 60000, "user": {"username": "u"},
             "permalink_url": "https://soundcloud.com/u/t", "access": "playable", "downloadable": False}
    p = SoundCloudProvider(_settings(soundcloud_client_id="c", soundcloud_client_secret="s"),
                           transport=_transport(_sc_handler(track)))
    res = await p.search("t")
    assert res[0]["id"] == "soundcloud:123" and res[0]["duration"] == 60
    stream = await p.get_stream("soundcloud:123")
    assert stream["url"].startswith("https://cf-media.sndcdn.com/")
    with pytest.raises(MediaRestrictedError, match="not enabled downloads"):
        await p.get_download("soundcloud:123")


@pytest.mark.asyncio
async def test_soundcloud_preview_only_track_is_not_streamed():
    track = {"id": 5, "title": "Go+", "access": "preview", "user": {}}
    p = SoundCloudProvider(_settings(soundcloud_client_id="c", soundcloud_client_secret="s"),
                           transport=_transport(_sc_handler(track)))
    with pytest.raises(MediaRestrictedError):
        await p.get_stream("soundcloud:5")


# --- mock / manager ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mock_results_are_labelled():
    results = await MockProvider().search("Raiden Shogun", limit=2)
    assert len(results) == 2 and all(r["mock"] for r in results)
    assert results[0]["id"].startswith("mock:")


@pytest.mark.asyncio
async def test_failing_provider_raises_recoverable():
    with pytest.raises(ProviderUnavailableError):
        await FailingProvider().search("x")


def test_error_taxonomy():
    assert ProviderTimeoutError("t").recoverable is True
    assert ProviderNotFoundError("nf").recoverable is False
    assert ProviderBadResponseError("b").code == "PROVIDER_BAD_RESPONSE"


def test_manager_youtube_first_by_default():
    m = ProviderManager(_settings(provider_order=""))
    assert m.order[:4] == ["youtube", "soundcloud", "spotify", "apple_music"]


def test_manager_order_configurable_and_lists_all():
    m = ProviderManager(_settings(provider_order="spotify,youtube", mock_provider_enabled=True))
    assert m.order[:2] == ["spotify", "youtube"] and m.order[-1] == "mock"
    names = {s.name for s in m.status()}
    assert {"youtube", "soundcloud", "spotify", "apple_music", "mock"} <= names


def test_mock_never_registered_in_production():
    m = ProviderManager(_settings(app_env="production", mock_provider_enabled=True))
    assert m.get("mock") is None and "mock" not in m.order


def test_provider_status_is_json_safe():
    m = ProviderManager(_settings())
    json.dumps([s.__dict__ for s in m.status()])
