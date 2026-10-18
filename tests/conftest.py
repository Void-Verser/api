"""Shared pytest fixtures: isolated sqlite DB, mock providers, clean limiter/cache."""

from __future__ import annotations

import os
import tempfile

# ---- Test env BEFORE app import (get_settings is cached at import time) ----
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_results_dir = tempfile.mkdtemp(prefix="mapi-results-")
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["REDIS_URL"] = ""
os.environ["MASTER_API_SECRET"] = "test-admin-secret"
os.environ["SIGNING_SECRET"] = "test-signing-secret"
os.environ["MOCK_PROVIDER_ENABLED"] = "true"
os.environ["PROVIDER_ORDER"] = "youtube,mock"
# No live network in automated tests: YouTube search is switched off and the
# other providers have no credentials.
os.environ["YOUTUBE_SEARCH_BACKEND"] = "off"
os.environ["YOUTUBE_API_KEY"] = ""
os.environ["MEDIA_EXTRACTION_ENABLED"] = "false"
os.environ["RATE_LIMIT_PER_MINUTE"] = "60"
os.environ["OWNER_EMAIL"] = "owner@example.com"
os.environ["OWNER_PASSWORD"] = "correct-horse-battery-staple"
os.environ["LOCAL_STORAGE_DIR"] = _results_dir
os.environ["STORAGE_BACKEND"] = "local"
for _k in ("SPOTIFY_CLIENT_ID", "SOUNDCLOUD_CLIENT_ID", "APPLE_MUSIC_DEVELOPER_TOKEN", "API_BASE_URL",
           "VERCEL", "VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
    os.environ.pop(_k, None)

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app import main as main_module  # noqa: E402
from app.database import get_session_factory, init_db, reset_engine_for_tests  # noqa: E402
from app.models.api_key import ApiKey  # noqa: E402
from app.models.job import Job, WorkerHeartbeat  # noqa: E402
from app.models.owner import OwnerSession  # noqa: E402
from app.services.cache import reset_cache_for_tests  # noqa: E402
from app.services.rate_limit import reset_rate_limiter_for_tests  # noqa: E402
from app.utils.security import generate_api_key, hash_api_key  # noqa: E402

TEST_DB_URL = f"sqlite:///{_tmp.name}"


@pytest.fixture(scope="session")
def _engine_once():
    reset_engine_for_tests(TEST_DB_URL)
    reset_cache_for_tests()
    reset_rate_limiter_for_tests()
    init_db()
    yield
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass


@pytest.fixture()
def client(_engine_once):
    reset_rate_limiter_for_tests()
    reset_cache_for_tests()
    # Wipe keys between tests for isolation.
    factory = get_session_factory()
    db = factory()
    try:
        for model in (Job, WorkerHeartbeat, OwnerSession, ApiKey):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    with TestClient(main_module.app) as c:
        yield c
    reset_rate_limiter_for_tests()
    reset_cache_for_tests()


@pytest.fixture()
def db_session(_engine_once):
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _insert_key(db, name="test", permissions=None, active=True, rate_limit=None, expired=False):
    plaintext = generate_api_key()
    record = ApiKey(
        name=name,
        key_hash=hash_api_key(plaintext),
        key_last3=plaintext[-3:],
        active=active,
        permissions=permissions or ["*"],
        rate_limit=rate_limit,
        request_count=0,
    )
    if expired:
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.add(record)
    db.commit()
    db.refresh(record)
    return plaintext, record


@pytest.fixture()
def make_key(db_session):
    def _make(**kwargs):
        return _insert_key(db_session, **kwargs)

    return _make


@pytest.fixture()
def auth_headers(make_key):
    plaintext, _ = make_key()
    return {"X-API-Key": plaintext}


ADMIN_HEADERS = {"X-Admin-Key": "test-admin-secret"}
OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def owner_client(client):
    """TestClient signed in as the bootstrapped owner; .csrf holds the token."""
    r = client.post("/dashboard/api/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    client.csrf = r.json()["data"]["csrf_token"]
    return client


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Automated tests must never reach real upstream services."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") and not host.startswith("/"):
            raise RuntimeError(f"live network access blocked in tests: {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
