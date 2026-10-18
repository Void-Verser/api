"""Database engine/session management.

Defaults to a local sqlite file so the API boots without any credentials.
Set DATABASE_URL to a PostgreSQL (or other SQLAlchemy-supported) URL for
production — sqlite on serverless (/tmp) is NOT persistent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        # Vercel's filesystem is read-only except /tmp (ephemeral).
        return "sqlite:////tmp/media_api.db" if os.getenv("VERCEL") else "sqlite:///./media_api.db"
    # Railway/Heroku style postgres:// -> SQLAlchemy + psycopg v3 dialect
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


def build_engine(url: str | None = None):
    from app.config import get_settings as _gs

    settings = _gs()
    db_url = normalize_database_url(url if url is not None else settings.database_url)
    if db_url.startswith("sqlite"):
        return create_engine(db_url, connect_args={"check_same_thread": False}, future=True)
    return create_engine(db_url, pool_pre_ping=True, future=True)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db():
    """FastAPI dependency yielding a DB session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


log = logging.getLogger("media_api.db")
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def is_persistent_database() -> bool:
    """False when data would vanish on redeploy (serverless sqlite)."""
    url = str(get_engine().url)
    if not url.startswith("sqlite"):
        return True
    return not (os.getenv("VERCEL") or url.startswith("sqlite:////tmp/"))


def run_migrations() -> None:
    """alembic upgrade head, adopting legacy create_all databases."""
    from alembic import command
    from alembic.config import Config

    engine = get_engine()
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            # Serialize concurrent cold starts.
            conn.execute(text("SELECT pg_advisory_xact_lock(724913)"))
        tables = set(inspect(conn).get_table_names())
        cfg.attributes["connection"] = conn
        if "alembic_version" not in tables and "api_keys" in tables:
            # Created by an older create_all() boot: schema matches 0001.
            command.stamp(cfg, "0001_initial")
        command.upgrade(cfg, "head")


def init_db() -> None:
    """Bring the schema up to date. Migrations are the source of truth;
    create_all is only a fallback if alembic cannot run."""
    import app.models  # noqa: F401  (populate metadata)

    settings = get_settings()
    if settings.run_migrations_on_startup and _MIGRATIONS_DIR.is_dir():
        try:
            run_migrations()
            return
        except Exception as exc:  # pragma: no cover - logged for operators
            log.error("migrations failed, falling back to create_all: %s", exc)
    Base.metadata.create_all(bind=get_engine())


def check_db() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_engine_for_tests(url: str):
    """Test helper: point the global engine at a throwaway database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = build_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine
