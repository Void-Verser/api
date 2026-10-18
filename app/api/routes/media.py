"""URL resolution, conversion jobs, job status and signed result files.

POST /v1/resolve   scope `resolve`  inspect a media URL + its real capabilities
POST /v1/convert   scope `convert`  queue an MP3/MP4 job (202 + job id)
GET  /v1/jobs/{id} scope `convert`  job status + expiring result URL
GET  /v1/files/... signed link      result download (local storage backend)
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_resolver, require_permission
from app.models.job import Job
from app.providers.base import CONVERT, DOWNLOAD, INFO, SEARCH, STREAM, ProviderError
from app.providers.soundcloud import SoundCloudProvider
from app.schemas.errors import APIError
from app.services import jobs as jobsvc
from app.services.media_urls import (
    ParsedMedia,
    UnsupportedURL,
    expand_short_link,
    is_short_link,
    parse_media_url,
)
from app.services.public_url import public_base_url
from app.services.resolver import (
    MediaResolver,
    ResolverError,
    ResolverNotFound,
    ResolverRestricted,
    route_media_id,
)
from app.services.storage import LocalStorage, get_storage, verify_file_link

router = APIRouter(prefix="/v1", tags=["media processing"])


class ResolveRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048, examples=["https://youtube.com/shorts/dQw4w9WgXcQ"])
    match_youtube: bool = Field(
        default=False,
        description="For catalog-only links (Spotify/Apple Music), also return YouTube search "
        "candidates. These are explicit search matches, never silent substitutions.",
    )


class ConvertRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048)
    id: Optional[str] = Field(default=None, max_length=256, description="e.g. youtube:dQw4w9WgXcQ")
    format: Literal["mp3", "mp4"] = "mp3"
    quality: Optional[Literal["128", "192", "320"]] = Field(
        default=None, description="MP3 bitrate (kbps). Default 192."
    )

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.url) == bool(self.id):
            raise ValueError("Provide exactly one of 'url' or 'id'.")
        return self


async def _parse(url: str) -> ParsedMedia:
    try:
        if is_short_link(url):
            url = await expand_short_link(url)
        return parse_media_url(url)
    except UnsupportedURL as exc:
        raise APIError(400, "UNSUPPORTED_URL", str(exc)) from exc


def _parsed_from_id(media_id: str) -> ParsedMedia:
    try:
        provider, routed = route_media_id(media_id)
    except ResolverError as exc:
        raise APIError(400, "BAD_REQUEST", str(exc)) from exc
    native = routed.split(":")[-1]
    if provider == "youtube":
        return parse_media_url(native)
    if provider == "soundcloud":
        return ParsedMedia("soundcloud", "track", routed, "", native)
    if provider in ("spotify", "apple_music"):
        return ParsedMedia(provider, "track", routed, "", native)
    raise APIError(400, "BAD_REQUEST", "Unrecognized media id. Use an id from /v1/search.")


async def _metadata(resolver: MediaResolver, parsed: ParsedMedia) -> tuple[dict | None, list[str]]:
    """Best-effort metadata. Not-found/restricted propagate; config gaps become warnings."""
    warnings: list[str] = []
    provider = resolver.manager.get(parsed.provider)
    try:
        if parsed.provider == "soundcloud" and parsed.id.startswith("https://"):
            if isinstance(provider, SoundCloudProvider) and provider.has_credentials:
                return await provider.resolve_permalink(parsed.canonical_url), warnings
            warnings.append("SoundCloud metadata needs SOUNDCLOUD_CLIENT_ID/SECRET.")
            return None, warnings
        if provider is None or not provider.supports(INFO):
            warnings.append(f"{parsed.provider} metadata is not configured on this server.")
            return None, warnings
        info, _ = await resolver.get_info(parsed.id)
        return info, warnings
    except (ResolverNotFound, ResolverRestricted):
        raise
    except ProviderError as exc:
        translated = resolver._translate(exc)
        if isinstance(translated, (ResolverNotFound, ResolverRestricted)):
            raise translated from exc
        warnings.append(f"Metadata lookup failed: {exc}")
    except ResolverError as exc:
        warnings.append(f"Metadata lookup failed: {exc}")
    return None, warnings


def _capabilities(resolver: MediaResolver, parsed: ParsedMedia, settings: Settings) -> dict:
    p = resolver.manager.get(parsed.provider)
    has = (lambda c: bool(p and p.enabled and p.supports(c)))
    convertible = has(CONVERT) and parsed.kind != "live"
    caps = {
        "metadata": has(INFO) or parsed.provider == "soundcloud" and has(SEARCH),
        "stream": has(STREAM),
        "download": has(DOWNLOAD),
        "convert": {"mp3": convertible, "mp4": convertible and parsed.provider == "youtube"},
    }
    notes = []
    if parsed.provider in ("spotify", "apple_music"):
        notes.append(f"{parsed.provider} is catalog-only: metadata and official links, no audio.")
    elif not settings.media_extraction_enabled:
        notes.append("Conversion/stream extraction is disabled on this server (MEDIA_EXTRACTION_ENABLED).")
    if parsed.kind == "live":
        notes.append("Live streams cannot be converted.")
    caps["notes"] = notes
    return caps


@router.post("/resolve", summary="Inspect a media URL and its supported operations")
async def resolve(
    request: Request,
    body: ResolveRequest,
    _key=Depends(require_permission("resolve")),
    resolver: MediaResolver = Depends(get_resolver),
    settings: Settings = Depends(get_settings),
):
    parsed = await _parse(body.url)
    media, warnings = await _metadata(resolver, parsed)
    data = {
        "source": {
            "provider": parsed.provider,
            "kind": parsed.kind,
            "id": (media or {}).get("id") or parsed.id,
            "canonical_url": parsed.canonical_url or (media or {}).get("url"),
            "is_short": parsed.kind == "short",
        },
        "media": media,
        "capabilities": _capabilities(resolver, parsed, settings),
        "warnings": warnings,
    }
    if body.match_youtube and parsed.provider in ("spotify", "apple_music") and media:
        query = f"{media.get('artist') or ''} {media.get('title') or ''}".strip()
        try:
            hits, _ = await resolver.search(query, limit=3, provider="youtube")
            data["youtube_candidates"] = {
                "note": "Search matches by artist/title — verify before use; not the same recording guaranteed.",
                "query": query,
                "results": hits,
            }
        except ResolverError as exc:
            data["youtube_candidates"] = {"error": str(exc), "results": []}
    request.state.provider = parsed.provider
    return {"success": True, "data": data}


@router.post("/convert", status_code=202, summary="Create an MP3/MP4 conversion job")
async def convert(
    request: Request,
    body: ConvertRequest,
    key=Depends(require_permission("convert")),
    resolver: MediaResolver = Depends(get_resolver),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    if not settings.media_extraction_enabled:
        raise APIError(503, "CONVERSION_DISABLED",
                       "Media conversion is disabled on this server (MEDIA_EXTRACTION_ENABLED=false).")
    parsed = await _parse(body.url) if body.url else _parsed_from_id(body.id or "")
    provider = resolver.manager.get(parsed.provider)
    if parsed.provider in ("spotify", "apple_music"):
        raise APIError(422, "UNSUPPORTED_OPERATION",
                       f"{parsed.provider} audio is DRM-protected and cannot be converted. "
                       "Use /v1/resolve with match_youtube=true to find a YouTube candidate.")
    if provider is None or not provider.supports(CONVERT):
        raise APIError(503, "CONVERSION_UNAVAILABLE",
                       f"Conversion for {parsed.provider} is not available (yt-dlp missing?).")
    if parsed.kind == "live":
        raise APIError(422, "UNSUPPORTED_OPERATION", "Live streams cannot be converted.")
    if body.format == "mp4" and parsed.provider != "youtube":
        raise APIError(422, "UNSUPPORTED_OPERATION", "MP4 output is only available for YouTube sources.")

    media, _warnings = await _metadata(resolver, parsed)
    source_url = parsed.canonical_url or (media or {}).get("url")
    if not source_url:
        raise APIError(422, "UNRESOLVABLE", "Could not determine a source URL for this id; send 'url'.")
    duration = (media or {}).get("duration")
    if duration and duration > settings.job_max_media_duration_seconds:
        raise APIError(422, "MEDIA_TOO_LONG",
                       f"Media is {duration}s; the limit is {settings.job_max_media_duration_seconds}s.")
    job = jobsvc.create_job(
        db, settings, api_key_id=key.id, provider=parsed.provider,
        media_id=(media or {}).get("id") or parsed.id, source_url=source_url,
        output_format=body.format, quality=body.quality if body.format == "mp3" else None,
        title=(media or {}).get("title"), duration=duration,
    )
    base, _ = public_base_url(settings, request)
    data = jobsvc.job_public(job, base, settings)
    if not jobsvc.workers_online(db, within_seconds=120):
        data["warning"] = "No worker is currently online; the job will wait in the queue."
    request.state.provider = parsed.provider
    return JSONResponse(status_code=202, content={"success": True, "data": data},
                        headers={"Location": data["status_url"]})


@router.get("/jobs/{job_id}", summary="Job status and result URL")
async def job_status(
    request: Request,
    job_id: str = Path(..., min_length=8, max_length=32),
    key=Depends(require_permission("convert")),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None or job.api_key_id != key.id:
        raise APIError(404, "NOT_FOUND", "Job not found.")
    base, _ = public_base_url(settings, request)
    return {"success": True, "data": jobsvc.job_public(job, base, settings)}


@router.get("/files/{job_id}/{filename}", summary="Signed, expiring result download",
            include_in_schema=False)
def job_file(
    job_id: str,
    filename: str,
    expires: int = Query(...),
    sig: str = Query(..., min_length=16, max_length=128),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    if not verify_file_link(job_id, expires, sig):
        raise APIError(403, "LINK_INVALID", "This download link is invalid or has expired.")
    job = db.get(Job, job_id)
    storage = get_storage(settings)
    if job is None or not job.result_key or not isinstance(storage, LocalStorage):
        raise APIError(404, "NOT_FOUND", "File not found.")
    path = storage.open_path(job.result_key)
    if path is None:
        raise APIError(404, "NOT_FOUND", "File not found or expired.")
    return FileResponse(path, media_type=job.result_content_type, filename=job.result_filename)
