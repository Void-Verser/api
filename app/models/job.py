"""Persistent media-processing jobs (the database is the job queue)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
EXPIRED = "expired"
ACTIVE_STATUSES = (QUEUED, RUNNING)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=QUEUED, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    media_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    output_format: Mapped[str] = mapped_column(String(8), nullable=False)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    result_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    capabilities: Mapped[str | None] = mapped_column(String(255), nullable=True)
