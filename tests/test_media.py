"""URL recognition, SSRF guards, /v1/resolve, conversion jobs + worker."""

from __future__ import annotations

import os
import time
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest

from app import main as main_module
from app.config import Settings, get_settings
from app.dependencies import get_resolver
from app.models.job import Job
from app.providers.manager import ProviderManager
from app.providers.youtube import YouTubeProvider
from app.services import jobs as jobsvc
from app.services.cache import CacheService
from app.services.media_urls import UnsupportedURL, expand_short_link, parse_media_url
from app.services.resolver import MediaResolver
from app.utils.netsec import UnsafeURLError, follow_redirects_safely, is_public_ip, validate_url_shape
from app.worker.processor import JobError, safe_filename
from app.worker.worker import run_worker

VID = "dQw4w9WgXcQ"


# --- URL recognition ----------------------------------------------------------
@pytest.mark.parametrize("url,kind", [
    (f"https://www.youtube.com/watch?v={VID}&t=42s", "video"),
    (f"https://m.youtube.com/watch?v={VID}", "video"),
    (f"https://music.youtube.com/watch?v={VID}&list=RD", "video"),
    (f"https://youtu.be/{VID}?si=abc", "video"),
    (f"https://www.youtube.com/shorts/{VID}", "short"),
    (f"https://youtube.com/shorts/{VID}?feature=share", "short"),
    (f"https://www.youtube.com/live/{VID}", "live"),
    (f"https://www.youtube.com/embed/{VID}", "video"),
    (f"https://www.youtube-nocookie.com/embed/{VID}", "video"),
    (f"youtube.com/shorts/{VID}", "short"),
    (VID, "video"),
])
def test_youtube_url_variants(url, kind):
    p = parse_media_url(url)
    assert (p.provider, p.kind, p.id) == ("youtube", kind, f"youtube:{VID}")
    assert p.canonical_url.startswith("https://www.youtube.com/")


def test_other_providers_urls():
    assert parse_media_url("https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=x").id == \
        "spotify:4cOdK2wGLETKBW3PvgPWqT"
    assert parse_media_url("spotify:track:4cOdK2wGLETKBW3PvgPWqT").provider == "spotify"
    assert parse_media_url("https://music.apple.com/us/album/x/1558533880?i=1558533900").id == "apple_music:1558533900"
    assert parse_media_url("https://music.apple.com/us/song/name/1558533900").id == "apple_music:1558533900"
    sc = parse_media_url("https://soundcloud.com/artist/track-name?in=x")
    assert sc.provider == "soundcloud" and sc.canonical_url == "https://soundcloud.com/artist/track-name"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/@channel",
    "https://open.spotify.com/album/4cOdK2wGLETKBW3PvgPWqT", "https://soundcloud.com/artist/sets/x",
    "https://example.com/video.mp4", "ftp://youtube.com/watch?v=" + VID, "file:///etc/passwd",
    "http://169.254.169.254/latest/meta-data", "",
])
def test_unsupported_urls_rejected(url):
    with pytest.raises(UnsupportedURL):
        parse_media_url(url)


# --- SSRF -----------------------------------------------------------------------
@pytest.mark.parametrize("ip,public", [
    ("127.0.0.1", False), ("10.1.2.3", False), ("192.168.1.1", False), ("169.254.169.254", False),
    ("100.64.0.1", False), ("0.0.0.0", False), ("::1", False), ("::ffff:127.0.0.1", False),
    ("fd00::1", False), ("8.8.8.8", True), ("2606:4700:4700::1111", True),
])
def test_public_ip_classification(ip, public):
    assert is_public_ip(ip) is public


@pytest.mark.parametrize("url", [
    "http://spotify.link/x", "https://user:pw@spotify.link/x", "https://spotify.link:8443/x",
    "https://127.0.0.1/x", "https://[::1]/x", "https://evil.example/x", "https://spotify.link.evil.example/x",
])
def test_url_shape_rejections(url):
    with pytest.raises(UnsafeURLError):
        validate_url_shape(url, {"spotify.link"})


@pytest.mark.asyncio
async def test_redirect_to_disallowed_or_internal_host_is_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    with pytest.raises(UnsafeURLError):
        await follow_redirects_safely("https://spotify.link/abc", {"spotify.link"},
                                      transport=httpx.MockTransport(handler), resolve_dns=False)

    def handler2(req):
        return httpx.Response(301, headers={"location": "https://internal.corp/admin"})

    with pytest.raises(UnsafeURLError):
        await follow_redirects_safely("https://spotify.link/abc", {"spotify.link"},
                                      transport=httpx.MockTransport(handler2), resolve_dns=False)


@pytest.mark.asyncio
async def test_short_link_expansion_revalidates_each_hop():
    def handler(req):
        if req.url.host == "spotify.link":
            return httpx.Response(302, headers={"location": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"})
        return httpx.Response(200)

    final = await expand_short_link("https://spotify.link/abc", transport=httpx.MockTransport(handler),
                                    resolve_dns=False)
    assert parse_media_url(final).id == "spotify:4cOdK2wGLETKBW3PvgPWqT"


@pytest.mark.asyncio
async def test_dns_to_private_address_blocked(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(UnsafeURLError, match="non-public"):
        await follow_redirects_safely("https://spotify.link/abc", {"spotify.link"},
                                      transport=httpx.MockTransport(lambda r: httpx.Response(200)))


# --- /v1/resolve + /v1/convert + worker ---------------------------------------------
def _oembed_transport():
    def handler(req):
        if req.url.path == "/oembed":
            return httpx.Response(200, json={"title": "Never Gonna Give You Up", "author_name": "Rick Astley",
                                             "thumbnail_url": "https://i.ytimg.com/x.jpg"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture()
def media_env(client, make_key):
    """Conversion enabled, YouTube metadata via mocked oEmbed."""
    settings = Settings(**{**get_settings().model_dump(), "media_extraction_enabled": True})
    yt = YouTubeProvider(settings, transport=_oembed_transport())

    def _resolver():
        return MediaResolver(ProviderManager(settings, extra={"youtube": yt}), CacheService(), settings)

    main_module.app.dependency_overrides[get_resolver] = _resolver
    main_module.app.dependency_overrides[get_settings] = lambda: settings
    plain, record = make_key()
    yield client, {"X-API-Key": plain}, record, settings
    main_module.app.dependency_overrides.clear()


def test_resolve_youtube_short(media_env):
    client, h, _, _ = media_env
    r = client.post("/v1/resolve", json={"url": f"https://youtube.com/shorts/{VID}"}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["source"] == {"provider": "youtube", "kind": "short", "id": f"youtube:{VID}",
                           "canonical_url": f"https://www.youtube.com/shorts/{VID}", "is_short": True}
    assert d["media"]["title"] == "Never Gonna Give You Up"
    assert d["capabilities"]["metadata"] is True


def test_resolve_catalog_only_provider_is_truthful(media_env):
    client, h, _, _ = media_env
    d = client.post("/v1/resolve", json={"url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"},
                    headers=h).json()["data"]
    assert d["capabilities"]["stream"] is False and d["capabilities"]["convert"] == {"mp3": False, "mp4": False}
    assert any("catalog-only" in n for n in d["capabilities"]["notes"])
    assert d["media"] is None and d["warnings"]


def test_resolve_rejects_unsupported_and_internal_urls(media_env):
    client, h, _, _ = media_env
    for url in ("http://169.254.169.254/latest", "https://example.com/a.mp4", "https://10.0.0.1/x"):
        r = client.post("/v1/resolve", json={"url": url}, headers=h)
        assert r.status_code == 400 and r.json()["error"]["code"] == "UNSUPPORTED_URL"


def test_convert_rejects_drm_catalog_and_bad_combos(media_env):
    client, h, _, _ = media_env
    r = client.post("/v1/convert", json={"url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}, headers=h)
    assert r.status_code == 422 and "DRM" in r.json()["error"]["message"]
    r = client.post("/v1/convert", json={"url": f"https://www.youtube.com/live/{VID}"}, headers=h)
    assert r.status_code == 422
    r = client.post("/v1/convert", json={"url": "https://youtu.be/" + VID, "id": "youtube:" + VID}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.post("/v1/convert", json={"url": "https://youtu.be/" + VID, "format": "flac"}, headers=h)
    assert r.status_code == 422


def _fake_processor(payload: bytes = b"ID3fake-mp3-bytes"):
    def process(spec, workdir):
        path = os.path.join(workdir, "out.mp3")
        with open(path, "wb") as fh:
            fh.write(payload)
        return {"path": path, "filename": safe_filename("Never Gonna / Give You Up", "x", "mp3"),
                "content_type": "audio/mpeg", "title": "Never Gonna Give You Up", "duration": 213}
    return process


def test_convert_job_end_to_end(media_env):
    client, h, record, settings = media_env
    r = client.post("/v1/convert", json={"url": f"https://youtube.com/shorts/{VID}", "format": "mp3",
                                         "quality": "320"}, headers=h)
    assert r.status_code == 202, r.text
    job = r.json()["data"]
    assert job["status"] == "queued" and job["provider"] == "youtube" and job["quality"] == "320"
    assert r.headers["location"].endswith(f"/v1/jobs/{job['job_id']}")
    assert "warning" in job  # no worker online yet

    assert run_worker(once=True, process_fn=_fake_processor()) == 1
    d = client.get(f"/v1/jobs/{job['job_id']}", headers=h).json()["data"]
    assert d["status"] == "succeeded" and d["result"]["size_bytes"] == len(b"ID3fake-mp3-bytes")
    assert d["result"]["filename"] == "Never Gonna Give You Up.mp3"

    url = urlsplit(d["result"]["url"])
    f = client.get(f"{url.path}?{url.query}")
    assert f.status_code == 200 and f.content == b"ID3fake-mp3-bytes"
    assert f.headers["content-type"] == "audio/mpeg"
    # Tampered / expired links are refused.
    assert client.get(f"{url.path}?expires={int(time.time()) + 999}&sig={'0' * 64}").status_code == 403
    assert client.get(url.path + "?expires=1&sig=" + "a" * 64).status_code == 403


def test_jobs_are_private_to_their_key(media_env, make_key):
    client, h, _, _ = media_env
    job_id = client.post("/v1/convert", json={"id": f"youtube:{VID}"}, headers=h).json()["data"]["job_id"]
    other, _ = make_key(name="other")
    assert client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": other}).status_code == 404


def test_worker_failure_retry_and_terminal(media_env, db_session):
    client, h, _, _ = media_env
    job_id = client.post("/v1/convert", json={"id": VID}, headers=h).json()["data"]["job_id"]

    def retryable(spec, workdir):
        raise JobError("PROVIDER_TEMPORARY", "network blip", retryable=True)

    run_worker(once=True, process_fn=retryable)
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job.status == "queued" and job.attempts == 1 and job.error_code == "PROVIDER_TEMPORARY"

    job.next_attempt_at = jobsvc.utcnow() - timedelta(seconds=1)
    db_session.commit()

    def private(spec, workdir):
        raise JobError("MEDIA_PRIVATE", "This video is private.")

    run_worker(once=True, process_fn=private)
    d = client.get(f"/v1/jobs/{job_id}", headers=h).json()["data"]
    assert d["status"] == "failed" and d["error"]["code"] == "MEDIA_PRIVATE"


def test_stale_running_job_is_requeued(media_env, db_session):
    client, h, _, settings = media_env
    job_id = client.post("/v1/convert", json={"id": VID}, headers=h).json()["data"]["job_id"]
    job = jobsvc.claim_next(db_session, "dead-worker", settings)
    assert job.id == job_id and job.status == "running"
    job.lease_expires_at = jobsvc.utcnow() - timedelta(seconds=5)
    db_session.commit()
    assert jobsvc.requeue_stale(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Job, job_id).status == "queued"


def test_expired_results_are_cleaned_up(media_env, db_session):
    client, h, _, settings = media_env
    job_id = client.post("/v1/convert", json={"id": VID}, headers=h).json()["data"]["job_id"]
    run_worker(once=True, process_fn=_fake_processor())
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    path = os.path.join(settings.local_storage_dir, job.result_key)
    assert os.path.exists(path)
    job.result_expires_at = jobsvc.utcnow() - timedelta(minutes=1)
    db_session.commit()
    assert client.get(f"/v1/jobs/{job_id}", headers=h).json()["data"]["status"] == "expired"
    assert jobsvc.cleanup_expired(db_session, settings) == 1
    assert not os.path.exists(path)


def test_active_job_limit_per_key(media_env):
    client, h, _, settings = media_env
    for _ in range(settings.max_active_jobs_per_key):
        assert client.post("/v1/convert", json={"id": VID}, headers=h).status_code == 202
    r = client.post("/v1/convert", json={"id": VID}, headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_ACTIVE_JOBS"


def test_worker_heartbeat_visible_while_running_and_removed_on_exit(media_env):
    client, _, _, _ = media_env
    seen = {}

    def process(spec, workdir):
        seen["workers"] = client.get("/health").json()["checks"]["workers_online"]
        return _fake_processor()(spec, workdir)

    h = media_env[1]
    client.post("/v1/convert", json={"id": VID}, headers=h)
    run_worker(once=True, process_fn=process)
    assert seen["workers"] == 1
    assert client.get("/health").json()["checks"]["workers_online"] == 0


def test_safe_filename():
    assert safe_filename('a/b\\c:*?"<>|', "x", "mp3") == "abc.mp3"
    assert safe_filename("", "fallback", "mp4") == "fallback.mp4"
    assert len(safe_filename("x" * 500, "f", "mp3")) <= 124
