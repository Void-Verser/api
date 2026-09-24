"""API surface: search / info / stream / download via mock provider."""

from __future__ import annotations


def _headers(make_key, **kw):
    plaintext, _ = make_key(**kw)
    return {"X-API-Key": plaintext}


def test_search_returns_normalized_results(client, make_key):
    h = _headers(make_key)
    r = client.get("/v1/search", params={"q": "Raiden Shogun", "limit": 3}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["query"] == "Raiden Shogun"
    assert len(data["results"]) == 3
    first = data["results"][0]
    for field in ("id", "title", "artist", "duration", "thumbnail", "provider"):
        assert field in first, f"missing {field}"
    assert first["provider"] == "mock"


def test_search_missing_query_rejected(client, make_key):
    h = _headers(make_key)
    r = client.get("/v1/search", headers=h)
    assert r.status_code in (400, 422)


def test_info_ok(client, make_key):
    h = _headers(make_key)
    r = client.get("/v1/search", params={"q": "x"}, headers=h)
    media_id = r.json()["data"]["results"][0]["id"]
    r = client.get(f"/v1/info/{media_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["id"] == media_id


def test_info_not_found(client, make_key):
    h = _headers(make_key)
    r = client.get("/v1/info/definitely-not-a-mock-id-zzz", headers=h)
    assert r.status_code == 404


def test_stream_ok(client, make_key):
    h = _headers(make_key)
    r = client.get("/v1/search", params={"q": "x"}, headers=h)
    media_id = r.json()["data"]["results"][0]["id"]
    r = client.get(f"/v1/stream/{media_id}", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["url"].startswith("https://")
    assert data["provider"] == "mock"


def test_download_ok(client, make_key):
    h = _headers(make_key)
    r = client.get("/v1/search", params={"q": "x"}, headers=h)
    media_id = r.json()["data"]["results"][0]["id"]
    r = client.get(f"/v1/download/{media_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["url"].startswith("https://")


def test_error_envelope_shape(client):
    r = client.get("/v1/search", params={"q": "x"}, headers={"X-API-Key": "mapi_bad"})
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_API_KEY"
    assert body["error"]["message"]
