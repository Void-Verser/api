# Deployment

The API and dashboard deploy to **Vercel** (preferred) or any Docker host.
Media conversion needs a **worker** on a Docker-capable host because
serverless functions can't run long FFmpeg jobs or keep files.

> A pushed commit is not a deployment. Verify with `GET /health` after each deploy.

## 1. Provision external services

| Need | Recommended | Env vars |
|---|---|---|
| Database (required) | Managed PostgreSQL: Neon, Supabase, Railway, RDS… | `DATABASE_URL` |
| Object storage (if converting on Vercel) | Cloudflare R2, AWS S3, Backblaze B2 | `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` |
| Redis (optional, multi-instance rate limits/cache) | Upstash, Railway | `REDIS_URL` |
| Worker host (if converting) | Railway, Fly.io, Render, any VPS | same DB + storage vars |

Add a bucket lifecycle rule deleting objects after a few days as a safety net
(the worker already deletes results after `RESULT_RETENTION_HOURS`).

Other SQL engines work through SQLAlchemy dialects (install the driver, e.g.
`pymysql`, and use its URL), but only SQLite and PostgreSQL are exercised by
this project; verify migrations before switching engines.

## 2. Deploy the API + dashboard to Vercel

1. Vercel → **Add New → Project** → import `Void-Verser/api`. Framework preset
   is detected (FastAPI; entrypoint `app/main.py`). No build command needed.
2. **Settings → Environment Variables** (Production), at minimum:
   - `APP_ENV=production`
   - `DATABASE_URL=postgresql://…`
   - `OWNER_EMAIL=you@example.com`, `OWNER_PASSWORD=<12+ chars>`
   - `SIGNING_SECRET=<openssl rand -hex 32>`
   - optional: `YOUTUBE_API_KEY`, `SPOTIFY_*`, `APPLE_MUSIC_*`, `SOUNDCLOUD_*`
   - for conversion: `MEDIA_EXTRACTION_ENABLED=true` + the `S3_*` vars
3. Deploy. Migrations run automatically on first request (cold start).
4. **Find your public URL**: Vercel shows it on the deployment
   (`https://<project>.vercel.app`). The app also detects it automatically from
   `VERCEL_PROJECT_PRODUCTION_URL` and shows it in the dashboard; set
   `API_BASE_URL` only for a custom domain.
5. Verify:
   ```bash
   curl https://<project>.vercel.app/health
   ```
   Expect `"status":"ok"` and `"database_persistent": true`.

CLI alternative: `npm i -g vercel && vercel link && vercel env add … && vercel deploy --prod`.

`vercel.json` sets `maxDuration: 60` for the function. The dashboard is
served by the same function (same origin, no CORS needed).

### Custom domain

Vercel → Project → **Domains** → add `api.example.com`, create the DNS record
Vercel shows, then set `API_BASE_URL=https://api.example.com` and redeploy.

## 3. Run the worker

Same image, different command. Give it the **same** `DATABASE_URL`,
`MEDIA_EXTRACTION_ENABLED=true`, storage vars, and `SIGNING_SECRET`.

```bash
docker build -t media-api .
docker run --env-file .env media-api python -m app.worker
```

Railway/Render/Fly: create a service from this repo (Dockerfile detected) and
set the start command to `python -m app.worker`. The dashboard shows
"Workers online" once it heartbeats. Run several workers for throughput.

YouTube may block datacenter IPs with a bot check (`PROVIDER_BLOCKED` job
error). Options: run the worker on a residential/less-flagged network, set
`YTDLP_PROXY`, or provide `YTDLP_COOKIES_FILE`. Only retrieve content you are
authorized to.

## 4. Single-host alternative (no Vercel)

```bash
cp .env.example .env    # fill OWNER_*, SIGNING_SECRET, MEDIA_EXTRACTION_ENABLED=true
docker compose up --build -d   # api + worker + postgres + redis, shared results volume
```

Put a TLS reverse proxy (Caddy, nginx, a platform router) in front of port
8000 and set `API_BASE_URL`. `STORAGE_BACKEND=local` works here because the
API and worker share the `results` volume. On a single small server you can
instead set `WORKER_EMBEDDED=true` and skip the worker service.

## 5. First sign-in and API key

1. Open `https://<your-url>/dashboard`, sign in with `OWNER_EMAIL` /
   `OWNER_PASSWORD`. (You may then delete `OWNER_PASSWORD` from the env; the
   hash is in the DB. Reset with `python scripts/create_owner.py --email … --reset-password`.)
2. Enter a name (e.g. `Raiden Music Bot`) and click **Get API Key**.
3. Click **Copy bot config** and put it in the bot's server-side environment:
   ```env
   MEDIA_API_URL=https://<your-url>
   MEDIA_API_KEY=mapi_...
   ```
4. Revoke or replace keys any time from the same page.
