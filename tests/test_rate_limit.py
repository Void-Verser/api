"""Per-key rate limiting: below limit passes, above limit -> 429."""

from __future__ import annotations


def test_below_limit_ok(client, make_key):
    plaintext, _ = make_key(rate_limit=10)
    for _ in range(3):
        r = client.get("/v1/search", params={"q": "hi"}, headers={"X-API-Key": plaintext})
        assert r.status_code == 200


def test_above_limit_429(client, make_key):
    plaintext, _ = make_key(rate_limit=3)
    codes = [
        client.get("/v1/search", params={"q": "hi"}, headers={"X-API-Key": plaintext}).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]
    # Error envelope is structured.
    r = client.get("/v1/search", params={"q": "hi"}, headers={"X-API-Key": plaintext})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"


def test_health_not_rate_limited(client):
    for _ in range(10):
        assert client.get("/health").status_code == 200
