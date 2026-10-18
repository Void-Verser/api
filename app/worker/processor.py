"""Downloads + converts one job's media with yt-dlp and FFmpeg.

Runs in a child process (see worker.run_in_subprocess) so a hung download
or conversion can be killed by the job timeout. Contract: argv[1] is a JSON
spec; one JSON result line is printed to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from app.config import get_settings
from app.services import ytdlp

CONTENT_TYPES = {"mp3": "audio/mpeg", "mp4": "video/mp4"}


class JobError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def safe_filename(title: str | None, fallback: str, ext: str) -> str:
    base = re.sub(r"[^\w\s.\-()&,'\[\]]", "", (title or "").strip(), flags=re.UNICODE)
    base = re.sub(r"\s+", " ", base).strip(" .")[:120] or fallback
    return f"{base}.{ext}"


def process_media(spec: dict, workdir: str) -> dict:
    settings = get_settings()
    try:
        import yt_dlp
    except ImportError as exc:
        raise JobError("WORKER_MISCONFIGURED", "yt-dlp is not installed on the worker.") from exc
    ffmpeg = ytdlp.find_ffmpeg(settings)
    if not ffmpeg:
        raise JobError("WORKER_MISCONFIGURED", "FFmpeg is not available on the worker.")

    fmt = spec["output_format"]
    h = settings.max_video_height
    opts = ytdlp.base_options(
        settings,
        outtmpl=os.path.join(workdir, "%(id)s.%(ext)s"),
        ffmpeg_location=ffmpeg,
        max_filesize=settings.job_max_output_mb * 1024 * 1024,
    )
    if fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3",
            "preferredquality": str(spec.get("quality") or "192"),
        }]
    elif fmt == "mp4":
        opts["format"] = (
            f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/"
            f"bv*[height<={h}]+ba/b[height<={h}]/b"
        )
        opts["merge_output_format"] = "mp4"
    else:
        raise JobError("INVALID_FORMAT", f"Unsupported output format '{fmt}'.")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(spec["source_url"], download=False)
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise JobError("MEDIA_LIVE", "Live streams and premieres cannot be converted.")
            if info.get("_type") == "playlist":
                raise JobError("UNSUPPORTED_MEDIA", "Playlists are not supported; send one item.")
            duration = info.get("duration")
            if duration and duration > settings.job_max_media_duration_seconds:
                raise JobError(
                    "MEDIA_TOO_LONG",
                    f"Media is {int(duration)}s; the limit is {settings.job_max_media_duration_seconds}s.",
                )
            ydl.process_ie_result(info, download=True)
    except JobError:
        raise
    except Exception as exc:
        c = ytdlp.classify_error(exc)
        raise JobError(c.code, c.message, c.retryable) from exc

    outputs = sorted(Path(workdir).glob(f"*.{fmt}"), key=lambda p: p.stat().st_size, reverse=True)
    if not outputs:
        raise JobError("PROCESSING_FAILED", "No output file was produced (size limit or conversion error).")
    return {
        "path": str(outputs[0]),
        "filename": safe_filename(info.get("title"), info.get("id") or "media", fmt),
        "content_type": CONTENT_TYPES[fmt],
        "title": info.get("title"),
        "duration": int(duration) if duration else None,
    }


def main() -> None:
    spec = json.loads(sys.argv[1])
    try:
        out = {"ok": True, **process_media(spec, spec["workdir"])}
    except JobError as exc:
        out = {"ok": False, "code": exc.code, "message": exc.message, "retryable": exc.retryable}
    except Exception as exc:  # pragma: no cover
        c = ytdlp.classify_error(exc)
        out = {"ok": False, "code": c.code, "message": c.message, "retryable": c.retryable}
    sys.stdout.write("\n" + json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
