"""Alembic migrations for the Media API.

Source of truth for the schema. app.database.init_db() runs these on
startup (RUN_MIGRATIONS_ON_STARTUP) so serverless deploys stay current.
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


def _url() -> str:
    # app.database.run_migrations passes the live URL via config attributes.
    return config.attributes.get("database_url") or normalize_database_url(
        os.getenv("DATABASE_URL", "")
    )


def run_migrations_offline() -> None:
    url = _url()
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
