"""Job queue on top of the relational database.

The jobs table is the queue: durable across restarts and portable to any
SQL database. Workers claim jobs with an atomic conditional UPDATE (lease),
so several workers can run safely; expired leases are re-queued.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import job as J
from app.models.job import Job, WorkerHeartbeat
from app.schemas.errors import APIError
from app.services.storage import get_storage


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def create_job(
    db: Session, settings: Settings, *, api_key_id: int | None, provider: str, media_id: str,
    source_url: str, output_format: str, quality: str | None, title: str | None,
    duration: int | None,
) -> Job:
    if api_key_id is not None and settings.max_active_jobs_per_key > 0:
        active = db.scalar(
            select(func.count(Job.id)).where(
                Job.api_key_id == api_key_id, Job.status.in_(J.ACTIVE_STATUSES)
            )
        ) or 0
        if active >= settings.max_active_jobs_per_key:
            raise APIError(
                429, "TOO_MANY_ACTIVE_JOBS",
                f"This key already has {active} active jobs (limit {settings.max_active_jobs_per_key}).",
            )
    job = Job(
        id=uuid.uuid4().hex, api_key_id=api_key_id, status=J.QUEUED, provider=provider,
        media_id=media_id, source_url=source_url, output_format=output_format, quality=quality,
        title=title, duration=duration, max_attempts=max(1, settings.job_max_attempts),
        next_attempt_at=utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def job_public(job: Job, base_url: str, settings: Settings) -> dict:
    data = {
        "job_id": job.id,
        "status": job.status,
        "provider": job.provider,
        "media_id": job.media_id,
        "source_url": job.source_url,
        "format": job.output_format,
        "quality": job.quality,
        "title": job.title,
        "duration": job.duration,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "status_url": f"{base_url}/v1/jobs/{job.id}",
        "result": None,
        "error": None,
    }
    if job.status == J.FAILED:
        data["error"] = {"code": job.error_code, "message": job.error_message}
    if job.status == J.EXPIRED:
        data["error"] = {"code": "RESULT_EXPIRED", "message": "The result file has expired and was deleted."}
    retention_end = _aware(job.result_expires_at)
    if job.status == J.SUCCEEDED and job.result_key and retention_end:
        if retention_end <= utcnow():
            data["status"] = J.EXPIRED
            data["error"] = {"code": "RESULT_EXPIRED", "message": "The result file has expired."}
        else:
            link_exp = min(int(time.time()) + settings.result_link_ttl_seconds, int(retention_end.timestamp()))
            data["result"] = {
                "url": get_storage(settings).url(
                    key=job.result_key, job_id=job.id, filename=job.result_filename or job.id,
                    content_type=job.result_content_type or "application/octet-stream",
                    expires_at=link_exp, base_url=base_url,
                ),
                "url_expires_at": datetime.fromtimestamp(link_exp, tz=timezone.utc).isoformat(),
                "filename": job.result_filename,
                "content_type": job.result_content_type,
                "size_bytes": job.result_size,
                "retained_until": retention_end.isoformat(),
            }
    return data


# --- worker side ---------------------------------------------------------------
def claim_next(db: Session, worker_id: str, settings: Settings) -> Job | None:
    now = utcnow()
    candidates = db.scalars(
        select(Job.id).where(Job.status == J.QUEUED, Job.next_attempt_at <= now)
        .order_by(Job.created_at).limit(5)
    ).all()
    for job_id in candidates:
        res = db.execute(
            update(Job).where(Job.id == job_id, Job.status == J.QUEUED).values(
                status=J.RUNNING, locked_by=worker_id, attempts=Job.attempts + 1,
                lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
                started_at=now, updated_at=now,
            )
        )
        db.commit()
        if res.rowcount == 1:
            return db.get(Job, job_id)
    return None


def mark_succeeded(db: Session, job: Job, *, key: str, filename: str, content_type: str,
                   size: int, settings: Settings, title: str | None, duration: int | None) -> None:
    now = utcnow()
    job.status = J.SUCCEEDED
    job.result_key, job.result_filename = key, filename
    job.result_content_type, job.result_size = content_type, size
    job.result_expires_at = now + timedelta(hours=settings.result_retention_hours)
    job.title = title or job.title
    job.duration = duration or job.duration
    job.finished_at, job.locked_by, job.lease_expires_at = now, None, None
    job.error_code = job.error_message = None
    db.commit()


def mark_failed(db: Session, job: Job, *, code: str, message: str, retryable: bool) -> None:
    now = utcnow()
    job.locked_by, job.lease_expires_at = None, None
    job.error_code, job.error_message = code, message[:2000]
    if retryable and job.attempts < job.max_attempts:
        job.status = J.QUEUED
        job.next_attempt_at = now + timedelta(seconds=30 * job.attempts)
    else:
        job.status = J.FAILED
        job.finished_at = now
    db.commit()


def requeue_stale(db: Session) -> int:
    """Running jobs whose worker died (lease expired) go back to the queue."""
    now = utcnow()
    stale = db.scalars(select(Job).where(Job.status == J.RUNNING, Job.lease_expires_at < now)).all()
    for job in stale:
        mark_failed(db, job, code="WORKER_LOST", message="Worker stopped before finishing.",
                    retryable=True)
    return len(stale)


def cleanup_expired(db: Session, settings: Settings) -> int:
    now = utcnow()
    jobs = db.scalars(
        select(Job).where(Job.status == J.SUCCEEDED, Job.result_expires_at < now).limit(100)
    ).all()
    storage = get_storage(settings)
    for job in jobs:
        if job.result_key:
            storage.delete(job.result_key)
        job.status, job.result_key = J.EXPIRED, None
    db.commit()
    return len(jobs)


def heartbeat(db: Session, worker_id: str, capabilities: str) -> None:
    hb = db.get(WorkerHeartbeat, worker_id)
    if hb is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, last_seen_at=utcnow(), capabilities=capabilities))
    else:
        hb.last_seen_at, hb.capabilities = utcnow(), capabilities
    db.commit()


def remove_heartbeat(db: Session, worker_id: str) -> None:
    hb = db.get(WorkerHeartbeat, worker_id)
    if hb is not None:
        db.delete(hb)
        db.commit()


def workers_online(db: Session, within_seconds: int = 60) -> list[dict]:
    cutoff = utcnow() - timedelta(seconds=within_seconds)
    rows = db.scalars(select(WorkerHeartbeat)).all()
    return [
        {"worker_id": r.worker_id, "last_seen_at": _aware(r.last_seen_at).isoformat(),
         "capabilities": r.capabilities}
        for r in rows if _aware(r.last_seen_at) >= cutoff
    ]


def job_counts(db: Session) -> dict:
    rows = db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    return {status: count for status, count in rows}
