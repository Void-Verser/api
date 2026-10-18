"""Worker loop: claim -> process -> store -> record, plus housekeeping."""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.services import jobs as jobsvc
from app.services import ytdlp
from app.services.storage import get_storage
from app.worker.processor import JobError

log = logging.getLogger("media_api.worker")
ProcessFn = Callable[[dict, str], dict]


def run_in_subprocess(spec: dict, workdir: str) -> dict:
    settings = get_settings()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.worker.processor", json.dumps({**spec, "workdir": workdir})],
            capture_output=True, text=True, timeout=settings.job_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise JobError("JOB_TIMEOUT", f"Processing exceeded {settings.job_timeout_seconds}s.", True) from exc
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("{")]
    if not lines:
        log.error("processor crashed rc=%s stderr=%s", proc.returncode, (proc.stderr or "")[-500:])
        raise JobError("PROCESSING_FAILED", "Processor crashed.", True)
    out = json.loads(lines[-1])
    if not out.pop("ok"):
        raise JobError(out["code"], out["message"], out.get("retryable", False))
    return out


def handle_job(db, job, settings: Settings, process_fn: ProcessFn) -> None:
    workdir = tempfile.mkdtemp(prefix=f"mapi-{job.id[:8]}-")
    try:
        spec = {"source_url": job.source_url, "output_format": job.output_format,
                "quality": job.quality, "job_id": job.id}
        out = process_fn(spec, workdir)
        key = f"{job.id}/{out['filename']}"
        size = get_storage(settings).put(out["path"], key, out["content_type"])
        jobsvc.mark_succeeded(
            db, job, key=key, filename=out["filename"], content_type=out["content_type"],
            size=size, settings=settings, title=out.get("title"), duration=out.get("duration"),
        )
        log.info("job %s succeeded (%s bytes)", job.id, size)
    except JobError as exc:
        log.info("job %s failed: %s %s", job.id, exc.code, exc.message)
        jobsvc.mark_failed(db, job, code=exc.code, message=exc.message, retryable=exc.retryable)
    except Exception as exc:
        log.exception("job %s crashed", job.id)
        jobsvc.mark_failed(db, job, code="PROCESSING_FAILED", message=type(exc).__name__, retryable=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_worker(*, once: bool = False, process_fn: ProcessFn | None = None,
               stop: threading.Event | None = None) -> int:
    """Process jobs until stopped. once=True drains the queue then returns."""
    settings = get_settings()
    process_fn = process_fn or run_in_subprocess
    worker_id = f"{socket.gethostname()[:40]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    caps = "convert" if ytdlp.ytdlp_available() and ytdlp.find_ffmpeg(settings) else "none"
    factory = get_session_factory()
    processed, last_maint = 0, 0.0
    while not (stop and stop.is_set()):
        db = factory()
        try:
            jobsvc.heartbeat(db, worker_id, caps)
            if time.time() - last_maint > 60:
                jobsvc.requeue_stale(db)
                jobsvc.cleanup_expired(db, settings)
                last_maint = time.time()
            job = jobsvc.claim_next(db, worker_id, settings)
            if job is not None:
                handle_job(db, job, settings, process_fn)
                processed += 1
                continue
        except Exception:
            log.exception("worker loop error")
        finally:
            db.close()
        if once:
            break
        time.sleep(settings.worker_poll_seconds)
    db = factory()
    try:  # clean exit: stop advertising this worker
        jobsvc.remove_heartbeat(db, worker_id)
    except Exception:
        pass
    finally:
        db.close()
    return processed


def start_embedded_worker() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=run_worker, kwargs={"stop": stop}, name="embedded-worker", daemon=True).start()
    return stop
