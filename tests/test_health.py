"""Health endpoint: no auth, provider outages must not kill it."""

from __future__ import annotations


def test_health_ok_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["version"]
    assert "X-Request-ID" in r.headers


def test_health_lists_providers(client):
    r = client.get("/health")
    names = {p["name"] for p in r.json().get("providers", [])}
    assert "mock" in names
    # Unconfigured providers report cleanly instead of crashing.
    by_name = {p["name"]: p for p in r.json()["providers"]}
    assert by_name["spotify"]["enabled"] is False
    assert "SPOTIFY_CLIENT_ID" in by_name["spotify"]["reason"]
    assert by_name["youtube"]["enabled"] is True  # metadata works without credentials


def test_readiness(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] == "up"


def test_docs_available(client):
    r = client.get("/docs")
    assert r.status_code == 200
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "Media API" in r.text
