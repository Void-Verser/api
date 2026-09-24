"""Alembic migrations for the Media API.

Production source of truth for the api_keys table. Fresh dev deploys also
work via app.database.init_db() (create_all), but schema changes must land
here as new versions.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alembic import context
from sqlalchemy import create_engine

from app.database import Base, normalize_database_url
import app.models  # noqa: F401  (register metadata)

config = context.config


def run_migrations_offline() -> None:
    url = normalize_database_url(os.getenv("DATABASE_URL", ""))
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = normalize_database_url(os.getenv("DATABASE_URL", ""))
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
