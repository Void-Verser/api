# Architecture

Actual, implemented architecture as of v2.0.0. Planned items are marked
**(planned)**. Keep this file in sync with the code.

```text
Telegram bots / other apps
        │  HTTPS + one client key (X-API-Key: mapi_...)
        ▼
Public URL (Vercel domain or custom domain)
        │
        ▼
FastAPI app  (app/main.py — also Vercel's zero-config entrypoint)
 ├── /v1/*            client API (unified key, per-key rate limit)
 ├── /dashboard       owner UI (static HTML/JS, same origin)
 ├── /dashboard/api/* owner JSON API (session cookie + CSRF)
 ├── /admin/*         server-to-server automation (X-Admin-Key)
 ├── /health, /health/ready, /docs
 │
 ├── MediaResolver (app/services/resolver.py) — capability-aware routing
 │      └── ProviderManager (PROVIDER_ORDER, YouTube first)
 │            ├── youtube      search, info [+ stream, download, convert]
 │            ├── soundcloud   search, info, stream, download [+ convert]
 │            ├── spotify      search, info            (catalog only)
 │            ├── apple_music  search, info            (catalog only)
 │            └── mock         dev/test only, never in production
 │
 └── Jobs API (/v1/convert, /v1/jobs) ──► jobs table (= durable queue)
                                              │
                    Worker (python -m app.worker; any Docker host)
                      claim (atomic lease) → yt-dlp → FFmpeg (MP3/MP4)
                      → storage (local volume or S3-compatible bucket)
                      → expiring result URL (HMAC link or S3 presigned)
```

## Components

| Concern | Implementation | Notes |
|---|---|---|
| HTTP framework | FastAPI, lifespan startup | `app/main.py` |
| Config | `pydantic-settings`, env only | `app/config.py`; nothing hard-coded |
| Database | SQLAlchemy 2 + Alembic | SQLite for dev/tests, PostgreSQL recommended in prod. Migrations run on startup (`RUN_MIGRATIONS_ON_STARTUP`, Postgres advisory lock); legacy `create_all` DBs are adopted (stamped 0001) |
| Client auth | `X-API-Key`, SHA-256 hash lookup | `app/auth/api_keys.py`; default permission `*` (all scopes) |
| Owner auth | scrypt password hash, DB sessions, HttpOnly SameSite=Strict cookie, CSRF header + Origin check, login throttling (per IP and per account) | `app/auth/owner_auth.py`, `app/api/dashboard.py`; no sign-up endpoint; bootstrap from `OWNER_EMAIL`/`OWNER_PASSWORD` or `scripts/create_owner.py` |
| Admin automation | `X-Admin-Key: MASTER_API_SECRET` | `app/api/admin/keys.py`; never used by the browser |
| Providers | `MediaProvider` + declared `capabilities` | `app/providers/*` |
| Resolver | prefixed IDs route only to their provider; search falls back across search-capable providers | `app/services/resolver.py` |
| URL recognition | offline parsing; YouTube watch/shorts/live/embed/youtu.be/music, Spotify track, Apple song, SoundCloud track | `app/services/media_urls.py` |
| SSRF guard | user URLs are never fetched except short-link expansion with allowlist, https/443, public-DNS check and per-hop revalidation | `app/utils/netsec.py` |
| Cache / rate limit | Redis if `REDIS_URL`, else in-memory | `app/services/cache.py`, `rate_limit.py` |
| Queue | `jobs` table, conditional-UPDATE claim with lease, retry w/ backoff, stale-lease requeue | `app/services/jobs.py` |
| Worker | subprocess per job with hard timeout; yt-dlp + FFmpeg | `app/worker/` |
| Storage | `local` (HMAC-signed `/v1/files` links) or `s3` (SigV4 presign, implemented without boto3) | `app/services/storage.py` |
| Public URL | `API_BASE_URL` → `VERCEL_PROJECT_PRODUCTION_URL` → `VERCEL_URL` → request | `app/services/public_url.py` |

## Key decisions

1. **One key per bot.** New keys get `["*"]`; scopes still exist for optional
   restricted keys created via `/admin/keys`.
2. **YouTube first.** `ProviderManager` puts `youtube` first unless
   `PROVIDER_ORDER` says otherwise. YouTube metadata works without credentials
   (oEmbed); Data API key enables quota-backed search; otherwise yt-dlp search.
3. **No silent substitution.** `spotify:ID` is never answered by YouTube.
   Cross-catalog matching only happens via `POST /v1/resolve` with
   `match_youtube=true`, labelled as search candidates.
4. **Capabilities are honest.** Spotify/Apple Music are catalog-only (DRM);
   operations they can't do return `422 UNSUPPORTED_OPERATION`.
5. **Extraction is opt-in.** `MEDIA_EXTRACTION_ENABLED=false` by default;
   the operator enables it only for content they are authorized to retrieve.
   No DRM circumvention; restricted media yields classified errors.
6. **Database as queue.** Durable and portable without extra infrastructure;
   multiple workers are safe. A dedicated broker can replace it later behind
   `app/services/jobs.py` if throughput requires **(planned only if needed)**.
7. **Vercel for API + dashboard, worker elsewhere.** Serverless functions
   can't run long FFmpeg jobs or keep files; the worker runs on any Docker host
   and shares only the database (+ S3 bucket) with the API.
8. **No third-party media API dependency.** A third-party reference API was
   examined once for diagnostics only; nothing in this codebase calls it.

## Deployment topologies

| Topology | API/dashboard | Worker | DB | Storage |
|---|---|---|---|---|
| Vercel (recommended start) | Vercel | Docker host (Railway/Fly/Render/VPS) | managed Postgres (Neon, Supabase, RDS…) | S3-compatible bucket (R2/S3/B2) |
| Single host | Docker (`docker compose up`) | compose `worker` service, or `WORKER_EMBEDDED=true` | compose Postgres | local volume |
| Metadata only | Vercel | none (`MEDIA_EXTRACTION_ENABLED=false`) | managed Postgres | none |

## Implementation checklist

- [x] Repository audit and baseline (36 tests passing before this work)
- [x] Unified client key (`*`), hashed storage, one-time display, revoke, rotate
- [x] Owner auth, dashboard UI, Get API Key, copy buttons, usage, provider/worker status
- [x] YouTube provider (Data API / yt-dlp search, oEmbed metadata, extraction-gated stream/download)
- [x] YouTube Shorts / youtu.be / live / embed / music URL recognition
- [x] Spotify, Apple Music (catalog), SoundCloud (official API + yt-dlp convert)
- [x] Capability-aware resolver; mock disabled in production
- [x] `/v1/resolve`, `/v1/convert`, `/v1/jobs/{id}`, signed `/v1/files`
- [x] Durable job queue, worker, retries, timeouts, cleanup, heartbeats
- [x] Local + S3-compatible storage with expiring links
- [x] SSRF protections
- [x] Alembic 0002 + startup migrations + legacy adoption
- [x] Vercel config (`vercel.json`, `.python-version`, `.vercelignore`), Docker image, compose with worker
- [x] Tests (mocked upstreams, network blocked) + live checks (see handoff)
- [ ] Live Vercel deployment and verification (needs owner's Vercel account)
- [ ] Verification against a real PostgreSQL instance
- [ ] Live verification of Spotify / Apple Music / SoundCloud API credentials
