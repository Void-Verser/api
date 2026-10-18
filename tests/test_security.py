"""Security helpers, storage signing, production config, migrations."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from sqlalchemy import inspect

from app.config import Settings
from app.services.storage import presign_s3_url, verify_file_link
from app.utils.security import (
    hash_password,
    redact_secrets,
    sign_value,
    verify_password,
)


def test_password_hashing_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h.startswith("scrypt$") and "correct" not in h
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # salted


def test_redaction_covers_keys_tokens_and_query_secrets():
    line = ("X-API-Key mapi_abcdefghijklmnop Bearer eyJhbGciOi OAuth sc-token "
            "https://h/x?api_key=SECRET1&sig=SECRET2")
    out = redact_secrets(line)
    for s in ("mapi_abcdefghijklmnop", "eyJhbGciOi", "sc-token", "SECRET1", "SECRET2"):
        assert s not in out


def test_s3_presign_matches_aws_reference_vector():
    # AWS SigV4 documentation example (query-string auth, GET test.txt).
    url = presign_s3_url(
        method="GET", bucket="examplebucket", key="test.txt", region="us-east-1",
        access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        expires=86400, now=datetime(2013, 5, 24, tzinfo=timezone.utc),
    )
    assert url.startswith("https://examplebucket.s3.amazonaws.com/test.txt?")
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


def test_s3_presign_custom_endpoint_is_path_style():
    url = presign_s3_url(method="PUT", bucket="b", key="job/a b.mp3", region="auto", access_key="k",
                         secret_key="s", expires=60, endpoint_url="https://acct.r2.cloudflarestorage.com")
    assert url.startswith("https://acct.r2.cloudflarestorage.com/b/job/a%20b.mp3?")


def test_file_link_signature_and_expiry():
    import time

    exp = int(time.time()) + 60
    sig = sign_value(f"job1:{exp}")
    assert verify_file_link("job1", exp, sig)
    assert not verify_file_link("job2", exp, sig)
    assert not verify_file_link("job1", int(time.time()) - 1, sign_value(f"job1:{int(time.time()) - 1}"))


def test_production_disables_mock_and_open_cors():
    s = Settings(_env_file=None, app_env="production", mock_provider_enabled=True)
    assert s.mock_enabled is False
    assert s.cors_origin_list == []
    assert Settings(_env_file=None, app_env="development", mock_provider_enabled=True).mock_enabled


def test_public_url_priority(monkeypatch):
    from app.services.public_url import public_base_url

    monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
    monkeypatch.delenv("VERCEL_URL", raising=False)
    s = Settings(_env_file=None, api_base_url="https://api.YOUR_DOMAIN.com")
    assert public_base_url(s)[1] == "unknown"  # placeholder ignored
    monkeypatch.setenv("VERCEL_URL", "proj-abc123.vercel.app")
    assert public_base_url(s) == ("https://proj-abc123.vercel.app", "VERCEL_URL")
    s2 = Settings(_env_file=None, api_base_url="https://media.example.org/")
    assert public_base_url(s2) == ("https://media.example.org", "API_BASE_URL")


def test_legacy_create_all_database_is_adopted_by_migrations():
    """A DB created by the previous release (api_keys only, no alembic table)."""
    from app import database

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(128) NOT NULL,
        key_hash VARCHAR(128) NOT NULL UNIQUE, key_last3 VARCHAR(8) NOT NULL, active BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL, expires_at DATETIME, last_used_at DATETIME,
        request_count INTEGER NOT NULL, rate_limit INTEGER, permissions JSON NOT NULL, metadata JSON)""")
    con.execute("""INSERT INTO api_keys (name,key_hash,key_last3,active,created_at,request_count,permissions)
        VALUES ('old','h','abc',1,'2026-01-01','0','["search"]')""")
    con.commit()
    con.close()
    from tests.conftest import TEST_DB_URL

    try:
        engine = database.reset_engine_for_tests(f"sqlite:///{path}")
        database.run_migrations()
        insp = inspect(engine)
        assert {"owners", "jobs", "owner_sessions", "worker_heartbeats", "alembic_version"} <= set(insp.get_table_names())
        assert "owner_id" in {c["name"] for c in insp.get_columns("api_keys")}
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT name FROM api_keys").scalar() == "old"  # data kept
    finally:
        database.reset_engine_for_tests(TEST_DB_URL)
        os.unlink(path)
