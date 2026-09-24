"""Shared pytest fixtures: isolated sqlite DB, mock providers, clean limiter/cache."""

from __future__ import annotations

import os
import tempfile

# ---- Test env BEFORE app import (get_settings is cached at import time) ----
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["REDIS_URL"] = ""
os.environ["MASTER_API_SECRET"] = "test-admin-secret"
os.environ["MOCK_PROVIDER_ENABLED"] = "true"
os.environ["PROVIDER_ORDER"] = "provider_a,provider_b,provider_c"
os.environ["RATE_LIMIT_PER_MINUTE"] = "60"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app import main as main_module  # noqa: E402
from app.database import get_session_factory, init_db, reset_engine_for_tests  # noqa: E402
from app.models.api_key import ApiKey  # noqa: E402
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
        db.query(ApiKey).delete()
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
        permissions=permissions or ["search", "info", "stream", "download"],
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
