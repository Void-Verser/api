"""Database engine/session management.

Defaults to a local sqlite file so the API boots without any credentials.
Set DATABASE_URL to a PostgreSQL URL for production/Railway.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return "sqlite:///./media_api.db"
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


def init_db() -> None:
    """Create tables if they do not exist (migrations are the source of truth
    for production; this keeps fresh deploys/dev working)."""
    # Import models so metadata is populated.
    import app.models  # noqa: F401

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
