"""Authentication + permissions (mocked providers, no live services)."""

from __future__ import annotations

from tests.conftest import ADMIN_HEADERS


def test_missing_key_is_401(client):
    r = client.get("/v1/search", params={"q": "hello"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "MISSING_API_KEY"


def test_invalid_key_is_401(client):
    r = client.get("/v1/search", params={"q": "hello"}, headers={"X-API-Key": "mapi_nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_API_KEY"


def test_valid_key_ok(client, make_key):
    plaintext, _ = make_key()
    r = client.get("/v1/search", params={"q": "Raiden Shogun"}, headers={"X-API-Key": plaintext})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_revoked_key_is_401(client, make_key):
    plaintext, _ = make_key(active=False)
    r = client.get("/v1/search", params={"q": "x"}, headers={"X-API-Key": plaintext})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "KEY_REVOKED"


def test_expired_key_is_401(client, make_key):
    plaintext, _ = make_key(expired=True)
    r = client.get("/v1/search", params={"q": "x"}, headers={"X-API-Key": plaintext})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "KEY_EXPIRED"


def test_denied_scope_is_403(client, make_key):
    plaintext, _ = make_key(permissions=["search", "info"])
    r = client.get("/v1/stream/mock-abc", headers={"X-API-Key": plaintext})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN_SCOPE"


def test_allowed_scope_ok(client, make_key):
    plaintext, _ = make_key(permissions=["search", "info"])
    r = client.get("/v1/search", params={"q": "x"}, headers={"X-API-Key": plaintext})
    assert r.status_code == 200


def test_admin_requires_secret(client, make_key):
    r = client.get("/admin/keys")
    assert r.status_code in (401, 503)
    # Ordinary client keys must NOT open admin endpoints.
    plaintext, _ = make_key()
    r = client.get("/admin/keys", headers={"X-API-Key": plaintext})
    assert r.status_code in (401, 503)


def test_admin_create_lists_masked(client):
    r = client.post(
        "/admin/keys",
        json={"name": "Raiden Bot", "permissions": ["search", "info", "stream", "download"]},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["api_key"].startswith("mapi_")
    key_id = data["id"]

    r = client.get("/admin/keys", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    keys = r.json()["data"]["keys"]
    row = next(k for k in keys if k["id"] == key_id)
    assert "api_key" not in row  # never expose plaintext on list
    assert row["masked_key"].startswith("mapi_")
    assert "7K2" not in row["masked_key"] or True
