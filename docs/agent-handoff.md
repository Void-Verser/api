# Agent handoff — current state

> New agent? Read `AGENTS.md` first, then this file, `docs/architecture.md`,
> and `README.md`. Verify against the code and `pytest` before trusting it.

## A. Project identity and goal

- Repo: https://github.com/Void-Verser/api (branch `main`)
- Goal: independently owned, YouTube-first media API for the owner's Telegram
  bots and other apps, with an owner dashboard that issues **one unified API
  key per bot** (search, info, stream, download, resolve, convert, jobs).
- Deployment strategy: Vercel for API + dashboard (preferred, not required);
  conversion worker on any Docker host; external Postgres + S3-compatible storage.

## B. Confirmed architectural decisions

- FastAPI app `app/main.py` (Vercel zero-config entrypoint), lifespan startup
  runs Alembic migrations and bootstraps the owner from `OWNER_EMAIL` / `OWNER_PASSWORD`.
- Client auth: `X-API-Key` → SHA-256 hash lookup (`app/auth/api_keys.py`);
  default permissions `["*"]`; per-key sliding-window rate limit.
- Owner auth: scrypt hashes, DB-backed sessions (`owner_sessions`), HttpOnly
  SameSite=Strict cookie, `X-CSRF-Token` + same-Origin check, login throttling
  per IP and per account. No sign-up route. `/admin/*` (X-Admin-Key) kept for
  server-side automation only.
- Providers (`app/providers/`): `youtube` (primary), `soundcloud`, `spotify`,
  `apple_music`, `mock` (dev only). Each declares `capabilities`. The resolver
  routes prefixed IDs (`youtube:…`) only to their provider; search falls back
  across search-capable providers in `PROVIDER_ORDER` (YouTube first).
- Media jobs: `jobs` table is the durable queue; worker (`python -m app.worker`)
  claims with an atomic lease, runs yt-dlp + FFmpeg in a subprocess with a hard
  timeout, stores to `local` (HMAC-signed `/v1/files` links) or `s3` (SigV4
  presigned, no boto3), records heartbeats, retries retryable errors, requeues
  stale leases, deletes expired results.
- Extraction/conversion is opt-in: `MEDIA_EXTRACTION_ENABLED=false` by default.
- Public URL: `API_BASE_URL` → `VERCEL_PROJECT_PRODUCTION_URL` → `VERCEL_URL` → request.
- No verified public deployment URL exists yet.

## C. Current implementation status

| Feature | Status |
|---|---|
| Owner dashboard (sign-in, Get API Key, copy URL/key/config, list, revoke, replace, providers, workers, examples) | Tested and passing (automated + manual browser check, Lighthouse a11y 100) |
| Unified key, hashed storage, one-time display, immediate revocation, rotation | Tested and passing |
| Owner auth / CSRF / login throttling / bootstrap | Tested and passing |
| YouTube search (Data API path) | Tested with mocks; **live Data API not tested** (no key available) |
| YouTube search (yt-dlp path), oEmbed metadata, Shorts resolution | Tested and passing (mocks) + **live verified** 2026-10-18 |
| YouTube conversion | Implemented; pipeline verified; **live YouTube download blocked from the sandbox IP** (bot check → correctly reported as `PROVIDER_BLOCKED`) |
| YouTube stream/download URL resolution | Implemented, mocked-path tested; not live-tested |
| SoundCloud API (search/info/stream/download) | Tested with mocks; live not tested (no credentials) |
| SoundCloud conversion via yt-dlp | **Live verified** (public track → 128 kbps MP3, signed link download) |
| Spotify catalog | Tested with mocks; live not tested (no credentials) |
| Apple Music catalog + ES256 token signing | Tested with mocks; live not tested (no credentials) |
| `/v1/resolve`, `/v1/convert`, `/v1/jobs`, `/v1/files` | Tested and passing |
| Job queue: retries, stale lease requeue, cleanup, per-key active-job limit, heartbeats | Tested and passing |
| S3 storage | Presign verified against the AWS SigV4 reference vector; **upload to a real bucket not tested** |
| SSRF protections | Tested and passing |
| Migrations (0001→0002, legacy adoption, CLI) | Tested on SQLite; **PostgreSQL not tested** (no server in sandbox) |
| Mock disabled in production | Tested and passing |
| Vercel config | Implemented; boot simulated with `VERCEL=1`; **not deployed** |
| Docker image / compose with worker | Implemented; **not built** (no Docker in sandbox) |
| Docs (README, architecture, deployment, AGENTS, this file) | Done |

## D. Files and changes (v2.0.0 work)

- New: `app/providers/{youtube,spotify,apple_music,soundcloud,http}.py`,
  `app/services/{media_urls,jobs,storage,keys,public_url,ytdlp}.py`,
  `app/utils/netsec.py`, `app/auth/owner_auth.py`, `app/api/dashboard.py`,
  `app/api/routes/media.py`, `app/dashboard/{index.html,app.js,styles.css}`,
  `app/models/{owner,job}.py`, `app/worker/{__init__,__main__,worker,processor}.py`,
  `migrations/versions/0002_owners_jobs.py`, `scripts/create_owner.py`,
  `vercel.json`, `.vercelignore`, `.python-version`, `.dockerignore`,
  `requirements-dev.txt`, `docs/{architecture,deployment,agent-handoff}.md`, `AGENTS.md`,
  tests `test_dashboard.py`, `test_media.py`, `test_security.py`.
- Rewritten: `app/config.py`, `app/providers/{base,manager,mock}.py`,
  `app/services/resolver.py`, routes `search/info/stream/download/health`,
  `README.md`, `.env.example`, `Dockerfile` (adds ffmpeg; one image for API + worker),
  `docker-compose.yml` (adds worker + shared volume), `tests/test_providers.py`, `tests/test_fallback.py`.
- Modified: `app/main.py` (lifespan, routers, error handlers, security headers),
  `app/database.py` (startup migrations, `/tmp` SQLite on Vercel, persistence check),
  `app/models/api_key.py` (`*` scope, `owner_id`), `app/api/admin/keys.py`
  (shared key service, default `*`), `app/utils/security.py` (scrypt, tokens,
  link signing, more redaction), `migrations/env.py`, `scripts/create_key.py`,
  `tests/conftest.py` (owner fixture, network blocker), `requirements.txt` (yt-dlp, PyJWT; dev deps moved).
- Removed: placeholder `app/providers/provider_a|b|c.py`, `http_base.py`.
- Why: replace placeholder providers with real capability-aware integrations,
  add owner dashboard + unified keys, add conversion jobs, prepare Vercel.

## E. Test and verification history (2026-10-18)

- `pytest` → **138 passed**, 0 failed (Python 3.13 venv; all upstreams mocked,
  live network blocked by fixture). Baseline before this work: 36 passed.
- `pyflakes` → only intentional side-effect imports reported.
- `alembic upgrade head` on fresh SQLite → `0002_owners_jobs (head)`.
- Live, manual, low-volume (real server on localhost):
  - dashboard sign-in → Get API Key → key works on `/v1/*` ✔
  - YouTube yt-dlp search "me at the zoo" ✔, oEmbed info ✔, Shorts resolve ✔
  - YouTube MP3 conversion ✘ — YouTube bot check from sandbox IP; job failed
    cleanly with `PROVIDER_BLOCKED` (expected environment limitation)
  - SoundCloud public track → MP3 128 kbps 3:33, downloaded via signed link ✔;
    tampered link → 403 ✔
  - Browser (Chrome DevTools): login, Get API Key, copy toast, revoke via
    confirm dialog → key rejected 401 ✔; mobile 390px layout ✔;
    Lighthouse: accessibility 100, best practices 100
- Not run: Vercel build/deploy, Docker build, PostgreSQL, real S3 upload,
  live Spotify/Apple/SoundCloud API or YouTube Data API (no credentials).

## F. Remaining tasks (priority order)

1. **Deploy to Vercel** (owner action: needs their Vercel account) and verify
   `/health`, dashboard sign-in, Get API Key on the real URL.
2. Provision managed **PostgreSQL** (`DATABASE_URL`) and verify migrations run there.
3. Deploy the **worker** (Docker host) with `MEDIA_EXTRACTION_ENABLED=true`,
   **S3-compatible storage** vars, and verify a YouTube conversion from that
   host's IP (may need `YTDLP_PROXY` / `YTDLP_COOKIES_FILE`).
4. Add credentials and live-verify: `YOUTUBE_API_KEY`, `SPOTIFY_CLIENT_ID/SECRET`,
   `APPLE_MUSIC_*`, `SOUNDCLOUD_CLIENT_ID/SECRET`.
5. Optional improvements: SoundCloud permalink metadata without credentials
   (via yt-dlp), per-key usage history table, password change in the dashboard,
   GitHub Actions CI running `pytest`.

Known limitations: YouTube may bot-check datacenter IPs; YouTube stream URLs
are short-lived and can be IP-bound (bots should use `/v1/convert`);
in-memory rate limiting is per-instance unless `REDIS_URL` is set.

## G. Exact next steps for the next agent

1. `git pull`, `pip install -r requirements-dev.txt`, `pytest` (expect 138 passed).
2. If the owner has deployed: hit `GET <url>/health`, record the verified URL
   in section H, and fix anything it reports.
3. If PostgreSQL is available: run the suite's migration path against it
   (`DATABASE_URL=postgresql://… alembic upgrade head`) and the app boot.
4. Do not rewrite the provider/resolver/job architecture; extend it.
   Do not re-add placeholder providers or any third-party media API dependency.

## H. Git and deployment state

- Branch: `main`.
- v2.0.0 implementation: commit `0039d3601b14aec07411e85c0c0d871e99a214c9`,
  **pushed** to `origin/main` and verified via the GitHub API (2026-10-18).
  This handoff update is the follow-up commit on top of it.
- Working tree after push: clean (no uncommitted changes).
- History note: commit `5332d73` (earlier session) mentions the third-party
  reference service by name in its message; it contains no URL or credential.
  History was not rewritten.
- Deployment: **not deployed**. No Vercel/Docker credentials were available in
  the session. No verified public URL.

## I. Last updated

2026-10-18 — Claude Code session (v2.0.0 implementation).
