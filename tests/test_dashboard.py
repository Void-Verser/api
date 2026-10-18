"""Owner dashboard: auth, CSRF, Get API Key, revoke/rotate, unified key."""

from __future__ import annotations

from sqlalchemy import select

from app.models.api_key import ApiKey
from app.utils.security import hash_api_key
from tests.conftest import ADMIN_HEADERS, OWNER_EMAIL, OWNER_PASSWORD, TEST_DB_URL


def _post(c, path, json=None, **headers):
    return c.post(path, json=json, headers={"X-CSRF-Token": c.csrf, **headers})


def test_dashboard_page_and_assets_served_with_csp(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Get API Key" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/dashboard/assets/app.js").status_code == 200
    assert client.get("/dashboard/assets/styles.css").status_code == 200
    assert client.get("/dashboard/assets/../config.py").status_code == 404


def test_root_redirects_browsers_to_dashboard(client):
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/dashboard"
    assert client.get("/", headers={"accept": "application/json"}).json()["data"]["docs"] == "/docs"


def test_session_requires_login_and_owner_was_bootstrapped(client):
    d = client.get("/dashboard/api/session").json()["data"]
    assert d == {"authenticated": False, "owner_configured": True}
    assert client.get("/dashboard/api/overview").status_code == 401
    assert client.get("/dashboard/api/keys").status_code == 401


def test_login_wrong_password_rejected(client):
    r = client.post("/dashboard/api/login", json={"email": OWNER_EMAIL, "password": "nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = client.post("/dashboard/api/login", json={"email": "x@y.z", "password": OWNER_PASSWORD})
    assert r.status_code == 401


def test_login_sets_secure_session_cookie(client):
    r = client.post("/dashboard/api/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "mapi_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert OWNER_PASSWORD not in r.text
    assert client.get("/dashboard/api/session").json()["data"]["authenticated"] is True


def test_login_is_rate_limited(client):
    codes = [client.post("/dashboard/api/login", json={"email": OWNER_EMAIL, "password": "bad"}).status_code
             for _ in range(12)]
    assert codes[-1] == 429


def test_overview_shows_real_url_and_providers(owner_client, monkeypatch):
    d = owner_client.get("/dashboard/api/overview").json()["data"]
    assert d["api_base_url"] == "http://testserver" and d["api_base_url_source"] == "request"
    assert {p["name"] for p in d["providers"]} >= {"youtube", "spotify", "apple_music", "soundcloud"}
    assert any("Mock provider" in w for w in d["warnings"])
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "media-api-example.vercel.app")
    d = owner_client.get("/dashboard/api/overview").json()["data"]
    assert d["api_base_url"] == "https://media-api-example.vercel.app"


def test_get_api_key_requires_csrf(owner_client):
    assert owner_client.post("/dashboard/api/keys", json={"name": "x"}).status_code == 403
    r = owner_client.post("/dashboard/api/keys", json={"name": "x"},
                          headers={"X-CSRF-Token": owner_client.csrf, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_get_api_key_flow_shows_key_once_and_stores_hash(owner_client, db_session):
    r = _post(owner_client, "/dashboard/api/keys", {"name": "Raiden Music Bot"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    key = data["api_key"]
    assert key.startswith("mapi_") and len(key) > 40
    assert data["permissions"] == ["*"] and data["name"] == "Raiden Music Bot"
    listing = owner_client.get("/dashboard/api/keys").json()["data"]["keys"]
    assert all("api_key" not in k for k in listing)
    assert key not in owner_client.get("/dashboard/api/keys").text
    row = db_session.scalar(select(ApiKey).where(ApiKey.id == data["id"]))
    assert row.key_hash == hash_api_key(key) and key not in (row.key_hash, row.key_last3)
    assert row.owner_id is not None


def test_one_key_works_across_all_client_operations(owner_client):
    key = _post(owner_client, "/dashboard/api/keys", {"name": "bot"}).json()["data"]["api_key"]
    h = {"X-API-Key": key}
    owner_client.cookies.clear()  # prove the key alone is enough
    hits = owner_client.get("/v1/search", params={"q": "hello"}, headers=h)
    assert hits.status_code == 200
    mid = hits.json()["data"]["results"][0]["id"]
    for path in (f"/v1/info/{mid}", f"/v1/stream/{mid}", f"/v1/download/{mid}"):
        assert owner_client.get(path, headers=h).status_code == 200, path
    r = owner_client.post("/v1/resolve", json={"url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"},
                          headers=h)
    assert r.status_code == 200
    # Conversion is disabled in this config: the key is accepted (no 401/403).
    r = owner_client.post("/v1/convert", json={"url": "https://youtu.be/dQw4w9WgXcQ"}, headers=h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "CONVERSION_DISABLED"
    assert owner_client.get("/v1/jobs/0123456789abcdef", headers=h).status_code == 404


def test_revocation_is_immediate(owner_client):
    d = _post(owner_client, "/dashboard/api/keys", {"name": "bot"}).json()["data"]
    h = {"X-API-Key": d["api_key"]}
    assert owner_client.get("/v1/search", params={"q": "a"}, headers=h).status_code == 200
    r = _post(owner_client, f"/dashboard/api/keys/{d['id']}/revoke")
    assert r.status_code == 200 and r.json()["data"]["status"] == "revoked"
    r = owner_client.get("/v1/search", params={"q": "a"}, headers=h)
    assert r.status_code == 401 and r.json()["error"]["code"] == "KEY_REVOKED"


def test_rotate_replaces_key(owner_client):
    old = _post(owner_client, "/dashboard/api/keys", {"name": "bot"}).json()["data"]
    new = _post(owner_client, f"/dashboard/api/keys/{old['id']}/rotate").json()["data"]
    assert new["api_key"] != old["api_key"] and new["name"] == "bot" and new["replaced_key_id"] == old["id"]
    assert owner_client.get("/v1/search", params={"q": "a"}, headers={"X-API-Key": old["api_key"]}).status_code == 401
    assert owner_client.get("/v1/search", params={"q": "a"}, headers={"X-API-Key": new["api_key"]}).status_code == 200


def test_client_key_cannot_reach_owner_or_admin_endpoints(owner_client):
    key = _post(owner_client, "/dashboard/api/keys", {"name": "bot"}).json()["data"]["api_key"]
    owner_client.cookies.clear()
    h = {"X-API-Key": key}
    assert owner_client.get("/dashboard/api/keys", headers=h).status_code == 401
    assert owner_client.post("/dashboard/api/keys", json={}, headers=h).status_code == 401
    assert owner_client.get("/admin/keys", headers=h).status_code == 401
    assert owner_client.get("/admin/keys", headers={"X-Admin-Key": key}).status_code == 401


def test_logout_invalidates_session(owner_client):
    assert _post(owner_client, "/dashboard/api/logout").status_code == 200
    assert owner_client.get("/dashboard/api/keys").status_code == 401


def test_admin_api_creates_unified_keys_by_default(client):
    r = client.post("/admin/keys", json={"name": "automation"}, headers=ADMIN_HEADERS)
    assert r.status_code == 200 and r.json()["data"]["permissions"] == ["*"]


def test_bootstrap_never_overwrites_or_accepts_weak_password(db_session):
    from app.auth.owner_auth import ensure_bootstrap_owner
    from app.config import Settings
    from app.models.owner import Owner

    before = db_session.scalar(select(Owner)).password_hash
    ensure_bootstrap_owner(db_session, Settings(_env_file=None, owner_email="new@example.com",
                                                owner_password="another-long-password"))
    owners = db_session.scalars(select(Owner)).all()
    assert len(owners) == 1 and owners[0].password_hash == before


def test_keys_persist_across_restart(owner_client):
    from app.database import init_db, reset_engine_for_tests

    key = _post(owner_client, "/dashboard/api/keys", {"name": "persist"}).json()["data"]["api_key"]
    reset_engine_for_tests(TEST_DB_URL)  # simulate a fresh process / redeploy
    init_db()
    r = owner_client.get("/v1/search", params={"q": "a"}, headers={"X-API-Key": key})
    assert r.status_code == 200
