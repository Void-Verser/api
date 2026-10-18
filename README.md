# Media API

YouTube-first media API for Telegram music bots and other apps: search,
metadata, Shorts/URL resolution, and MP3/MP4 conversion jobs behind **one API
key per bot**, with an owner dashboard for issuing and revoking keys.

Bots only need two values:

```env
MEDIA_API_URL=https://your-deployment.example
MEDIA_API_KEY=mapi_xxxxxxxxxxxxxxxx
```

- Architecture: [`docs/architecture.md`](docs/architecture.md)
- Deployment (Vercel + worker, or single host): [`docs/deployment.md`](docs/deployment.md)
- Current status / next steps for contributors and agents: [`docs/agent-handoff.md`](docs/agent-handoff.md), [`AGENTS.md`](AGENTS.md)

## Features

- **Owner dashboard** (`/dashboard`): secure sign-in, **Get API Key** (shown
  once, copy key / copy bot config), masked key list with usage, revoke,
  replace, real public URL, provider + worker status, copyable examples.
- **Unified client key**: `X-API-Key: mapi_…` works for search, info, stream,
  download, resolve, convert and job status. Keys are random, stored only as
  SHA-256 hashes, revocable instantly, individually rate-limited.
- **Providers** with honest capabilities (see below). YouTube is primary.
- **Conversion jobs**: `POST /v1/convert` → durable queue → worker (yt-dlp +
  FFmpeg) → expiring download link. MP3 (128/192/320 kbps) and MP4 (≤720p by default).
- **Portable**: Vercel or Docker; SQLite (dev) / PostgreSQL (prod); Redis
  optional; local or S3-compatible storage.
- **Security**: owner sessions with CSRF protection, SSRF-safe URL handling,
  secret redaction in logs, strict CSP on the dashboard, no mock data in production.

## Providers

| Provider | Search | Metadata | Stream URL | Download URL | Convert (MP3/MP4) | Needs |
|---|---|---|---|---|---|---|
| **YouTube** (primary) | ✅ Data API or yt-dlp | ✅ Data API or oEmbed | ⚙️ | ⚙️ | ⚙️ MP3 + MP4 | nothing for metadata; `YOUTUBE_API_KEY` recommended |
| SoundCloud | ✅ | ✅ | ✅ playable tracks | ✅ if uploader allows | ⚙️ MP3 | `SOUNDCLOUD_CLIENT_ID/SECRET` (convert works without) |
| Spotify | ✅ | ✅ | ❌ | ❌ | ❌ (DRM) | `SPOTIFY_CLIENT_ID/SECRET` |
| Apple Music | ✅ | ✅ (+30s `preview_url` as metadata) | ❌ | ❌ | ❌ (DRM) | developer token or MusicKit key |

⚙️ = requires `MEDIA_EXTRACTION_ENABLED=true` (off by default; enable only for
content you're authorized to retrieve). Unsupported operations return
`422 UNSUPPORTED_OPERATION`, unconfigured providers `503 PROVIDER_UNAVAILABLE`
— never fake success. For a Spotify/Apple link, `POST /v1/resolve` with
`"match_youtube": true` returns clearly-labelled YouTube **search candidates**;
nothing is substituted silently.

Recognized URLs: `youtube.com/watch?v=`, `youtube.com/shorts/…`, `youtu.be/…`,
`youtube.com/live|embed/…`, `music.youtube.com`, `m.youtube.com`, bare video
IDs, `open.spotify.com/track/…`, `spotify:track:…`, `spotify.link` short links,
`music.apple.com/…/song/…` and album links with `?i=`, `soundcloud.com/user/track`,
`on.soundcloud.com` short links.

## API

All `/v1/*` routes need `X-API-Key`. IDs are provider-prefixed
(`youtube:dQw4w9WgXcQ`); bare 11-char IDs are treated as YouTube.

| Method & path | Purpose |
|---|---|
| `GET /health`, `GET /health/ready` | liveness / readiness (no auth) |
| `GET /v1/search?q=&limit=&offset=&provider=` | search (YouTube first, falls back across search-capable providers) |
| `GET /v1/info/{id}` | normalized metadata |
| `GET /v1/stream/{id}` | short-lived playable URL where supported |
| `GET /v1/download/{id}` | direct download URL where supported |
| `POST /v1/resolve` `{url, match_youtube?}` | identify a link (incl. Shorts), metadata + real capabilities |
| `POST /v1/convert` `{url \| id, format: mp3\|mp4, quality?}` | create job → `202` + `job_id` |
| `GET /v1/jobs/{job_id}` | status; when `succeeded`, `result.url` (expiring) |
| `/dashboard` | owner UI |
| `/admin/*` | server-side automation with `X-Admin-Key` (optional) |
| `/docs`, `/redoc`, `/openapi.json` | interactive docs |

Errors: `{"success": false, "error": {"code", "message", "request_id"}}`.
Every response has `X-Request-ID`.

### Telegram bot example

```python
import os, time, httpx

BASE = os.environ["MEDIA_API_URL"]
H = {"X-API-Key": os.environ["MEDIA_API_KEY"]}

hit = httpx.get(f"{BASE}/v1/search", params={"q": "Raiden Shogun"}, headers=H).json()["data"]["results"][0]
job = httpx.post(f"{BASE}/v1/convert", json={"id": hit["id"], "format": "mp3"}, headers=H).json()["data"]
while job["status"] in ("queued", "running"):
    time.sleep(2)
    job = httpx.get(job["status_url"], headers=H).json()["data"]
if job["status"] == "succeeded":
    audio_url = job["result"]["url"]   # expiring link: download it or pass to sendAudio
else:
    print(job["error"])                # e.g. MEDIA_PRIVATE, MEDIA_TOO_LONG, PROVIDER_BLOCKED
```

Shorts work the same: `{"url": "https://youtube.com/shorts/VIDEO_ID", "format": "mp4"}`.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # set OWNER_EMAIL / OWNER_PASSWORD; MOCK_PROVIDER_ENABLED=true for fake data
uvicorn app.main:app --reload
# conversions (optional): MEDIA_EXTRACTION_ENABLED=true, then in another shell:
python -m app.worker
```

Open http://localhost:8000/dashboard. `requirements-dev.txt` includes
`imageio-ffmpeg`, so no system FFmpeg is needed locally.

Scripts: `scripts/create_owner.py` (create owner / reset password, prompts
for the password), `scripts/create_key.py` (issue a key from the shell).

## Configuration

Every variable is documented in [`.env.example`](.env.example) (placeholders
only). Essentials for production: `APP_ENV=production`, `DATABASE_URL`,
`OWNER_EMAIL`, `OWNER_PASSWORD` (first boot), `SIGNING_SECRET`. Optional
integrations may be left blank — the API boots and reports what's missing in
`/health` and the dashboard. Abuse safeguards (`RATE_LIMIT_PER_MINUTE`,
`MAX_ACTIVE_JOBS_PER_KEY`, job duration/size/time limits) are configurable;
there are no daily or monthly quotas. Upstream provider quotas and your
hosting limits still apply.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The suite mocks all upstreams and blocks real network access. See
`docs/agent-handoff.md` for the latest results and which live checks were run.

## Security notes

- Never commit `.env`; production secrets belong in your host's secret manager.
- Client keys can't reach `/dashboard/api/*` or `/admin/*`; the admin secret
  never reaches the browser.
- User-supplied URLs are parsed offline; only allowlisted short-link hosts are
  ever fetched, over HTTPS, with public-IP checks on every redirect hop.
- No DRM circumvention. Restricted, private or region-blocked media fails
  with a specific error code.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "No owner account exists" | set `OWNER_EMAIL` + `OWNER_PASSWORD` (12+ chars) and redeploy, or run `scripts/create_owner.py` |
| `database_persistent: false` | set `DATABASE_URL` (SQLite on Vercel lives in `/tmp` and is wiped) |
| `503 CONVERSION_DISABLED` | set `MEDIA_EXTRACTION_ENABLED=true` and run a worker |
| Jobs stay `queued` | no worker online — check dashboard "Workers online" and worker logs |
| Job error `PROVIDER_BLOCKED` | YouTube bot check on the worker's IP — use `YTDLP_PROXY`/`YTDLP_COOKIES_FILE` or another host |
| `422 UNSUPPORTED_OPERATION` | the provider genuinely can't do that (e.g. Spotify audio) |
| `401 KEY_REVOKED` | key was revoked/replaced in the dashboard |
| Result link `403` | link expired — fetch `GET /v1/jobs/{id}` again for a fresh link |
