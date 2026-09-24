# Media API

Centralized, production-ready **Media API** for Telegram music bots and any other
application that needs music/media **search, metadata, stream resolution, and
download resolution** through one stable interface.

Clients know only two things:

```env
MEDIA_API_URL=https://api.YOUR_DOMAIN.com
MEDIA_API_KEY=mapi_xxxxxxxxxxxxxxxxx
```

Everything else — which provider served the result, provider keys/URLs,
fallback order, cache, rate limiter, database layout — lives inside this
service. Providers can be added, removed, replaced, or reordered without
touching API routes or Telegram bots.

---

## 1. Project overview

- **Public contract (versioned):** `GET /v1/search`, `GET /v1/info/{id}`,
  `GET /v1/stream/{id}`, `GET /v1/download/{id}`, plus `GET /health` and
  `/admin/*` for key management.
- **Auth:** per-client API keys (`X-API-Key: mapi_...`, hashed at rest,
  revocable, expirable, permission-scoped, individually rate-limited).
- **Providers:** `MediaProvider` interface + `ProviderManager` + central
  `MediaResolver` with fallback. Results are normalized to one schema no
  matter which provider answered.
- **Infra:** FastAPI + PostgreSQL + Redis (+ graceful in-memory fallbacks so
  dev/test boot with zero credentials), Docker + docker-compose, Railway-ready,
  custom-domain-ready.

## 2. Architecture

```text
Telegram Bot / Other App
        │  X-API-Key: MEDIA_API_KEY
        ▼
MEDIA_API_URL (https://api.YOUR_DOMAIN.com)
        │
        ▼
Reverse Proxy (HTTPS / domain)
        │
        ▼
FastAPI (Media API)
        │
  Request-ID + Logging middleware
        │
Authentication → Rate Limit → Permission check
        │
  API Routes (/v1/search|info|stream|download)
        │
  Media Resolver (owns fallback + normalization)
        │
  Provider Manager (configurable order)
        │
Provider A · Provider B · Provider C · Mock
        │
  Normalized MediaResult
        │
  Redis cache (TTL) ──→ Response (+ X-Request-ID)
        │
  PostgreSQL (API keys, usage)
```

## 3. Features

- Versioned client API (`/v1/...`), OpenAPI at `/docs`, `/redoc`, `/openapi.json`
- Secure API keys: `mapi_` + 43-char crypto-random secret, SHA-256 hashed storage,
  masked listing (`mapi_****************VB8`), revocation, expiry, scopes
  (`search`, `info`, `stream`, `download`), per-key rate limits, usage tracking
- Separate admin auth (`X-Admin-Key: MASTER_API_SECRET`) — client keys never work on `/admin/*`
- Provider abstraction with fallback (recoverable errors fall through;
  invalid queries never fan out), per-provider timeouts
- Redis caching (search/metadata TTL) with memory fallback; per-key sliding-window
  rate limiting (Redis or memory); `/health` is never rate-limited
- Request IDs on every response, structured logs with secret redaction,
  consistent `{success, data|error}` envelopes, CORS via env
- Boots with **no provider credentials**: unconfigured providers report
  `credentials not configured` instead of crashing; `/health`, `/docs` always work

## 4. Project structure

```text
app/
  main.py            # FastAPI app, middleware, routers, exception handlers
  config.py          # env-driven Settings (domain configurable, no hard-coding)
  database.py        # engine/session, sqlite default, postgres in prod
  dependencies.py    # auth + permission + rate-limit + resolver wiring
  api/routes/        # health.py, search.py, info.py, stream.py, download.py
  api/admin/         # keys.py (CRUD, revoke/reactivate, stats)
  auth/              # api_keys.py (X-API-Key), admin_auth.py (X-Admin-Key)
  models/            # api_key.py
  schemas/           # media.py, search.py, errors.py, admin.py
  providers/         # base.py, manager.py, provider_a/b/c.py, http_base.py, mock.py
  services/          # resolver.py, cache.py, rate_limit.py, media.py
  middleware/        # request_id.py, logging.py
  utils/             # security.py (generate/hash/mask/redact)
migrations/          # alembic env + versions/0001_initial.py
tests/               # health/auth/rate_limit/search/providers/fallback
scripts/create_key.py
Dockerfile  docker-compose.yml  requirements.txt  alembic.ini
.env.example  pytest.ini
```

## 5. Local setup

```bash
git clone https://github.com/Void-Verser/api.git
cd api
cp .env.example .env   # then edit: at minimum set MASTER_API_SECRET
```

## 6. Python setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

## 7. PostgreSQL setup

Production/Railway: create a Postgres database and set, e.g.:

```env
DATABASE_URL=postgresql+psycopg://user:password@host:5432/media_api
```

(`postgres://...` URLs are auto-normalized.) Then:

```bash
alembic upgrade head
```

Local dev may skip Postgres entirely — with `DATABASE_URL` empty the app uses
`./media_api.db` (sqlite) and creates tables on startup.

## 8. Redis setup

Optional locally. With `REDIS_URL` empty the app uses in-memory cache + rate
limiter (single instance). Production:

```env
REDIS_URL=redis://localhost:6379/0
```

## 9. Environment variables

See `.env.example` (complete, no real secrets):

| Var | Purpose | Required in prod |
|---|---|---|
| `APP_ENV` | `development`/`production` (error detail, CORS default) | yes |
| `API_BASE_URL` | public URL, e.g. `https://api.YOUR_DOMAIN.com` | yes |
| `DATABASE_URL` | postgres URL (empty → local sqlite) | yes |
| `REDIS_URL` | redis URL (empty → memory fallback) | yes |
| `MASTER_API_SECRET` | admin secret for `/admin/*` | yes |
| `CORS_ORIGINS` | comma list (`*` only for dev) | yes |
| `LOG_LEVEL` | e.g. `INFO` | no |
| `PROVIDER_ORDER` | e.g. `provider_a,provider_b,provider_c` | no |
| `MOCK_PROVIDER_ENABLED` | `true` dev / `false` prod | yes (false in prod) |
| `PROVIDER_TIMEOUT_SECONDS` | upstream timeout | no |
| `PROVIDER_A/B/C_API_KEY`, `PROVIDER_A/B/C_BASE_URL` | upstream creds (optional at boot) | when wiring real providers |
| `RATE_LIMIT_PER_MINUTE` | default per-key limit | no |
| `CACHE_TTL_SEARCH/INFO_SECONDS` | cache TTLs | no |
| `PORT` | server port (Railway injects) | handled by platform |

## 10. API authentication

Clients send the key on every `/v1/*` call:

```http
X-API-Key: mapi_xxxxxxxxxxxxxxxxx
```

Missing → `401 MISSING_API_KEY`, unknown → `401 INVALID_API_KEY`,
deactivated → `401 KEY_REVOKED`, past `expires_at` → `401 KEY_EXPIRED`,
wrong scope → `403 FORBIDDEN_SCOPE`, over limit → `429 RATE_LIMITED`.

## 11. API-key generation

Via admin API (plaintext shown **once**):

```bash
curl -X POST "$MEDIA_API_URL/admin/keys" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $MASTER_API_SECRET" \
  -d '{"name":"Raiden Bot","permissions":["search","info","stream","download"]}'
```

Or directly against the DB (same one-time output):

```bash
python scripts/create_key.py --name "Raiden Bot" \
  --permissions search info stream download
```

List/revoke/reactivate/delete/update afterwards — list endpoints only ever
show `masked_key`.

## 12. API endpoints

| Method & path | Auth/scope | Notes |
|---|---|---|
| `GET /health` | none | Railway/uptime; `{status, version, checks, providers}`; never 500s on provider outage |
| `GET /v1/search?q=&limit=&offset=` | key + `search` | normalized results + serving `provider` |
| `GET /v1/info/{id}` | key + `info` | normalized metadata |
| `GET /v1/stream/{id}` | key + `stream` | temporary URL, `expires_at` when applicable |
| `GET /v1/download/{id}` | key + `download` | temporary URL + filename when applicable |
| `POST /admin/keys` | admin | create (plaintext once) |
| `GET /admin/keys`, `GET /admin/keys/{id}` | admin | masked |
| `PATCH /admin/keys/{id}` | admin | name/permissions/rate_limit/active |
| `POST /admin/keys/{id}/revoke`, `.../reactivate` | admin | |
| `DELETE /admin/keys/{id}` | admin | |
| `GET /admin/stats` | admin | totals |
| `GET /docs`, `/redoc`, `/openapi.json` | none | |

Example:

```bash
curl -H "X-API-Key: YOUR_MEDIA_API_KEY" \
  "https://api.YOUR_DOMAIN.com/v1/search?q=Raiden%20Shogun"
```

Every response carries `X-Request-ID` — include it when reporting issues.

## 13. Provider system

- `app/providers/base.py` — `MediaProvider` interface
  (`search/get_info/get_stream/get_download`) + error taxonomy.
- `app/providers/http_base.py` — shared httpx skeleton with timeout handling.
- `app/providers/provider_a.py`, `provider_b.py`, `provider_c.py` — integration
  points. With no env credentials they raise `ProviderUnavailableError`
  (`credentials not configured`); add real upstream mapping inside
  `_call_upstream`/`_normalize_item` without touching routes.
- `app/providers/mock.py` — deterministic fake provider for dev/test.
  Disable in production (`MOCK_PROVIDER_ENABLED=false`).
- `app/providers/manager.py` — registry + `PROVIDER_ORDER`; `mock` is appended
  last unless explicitly ordered. `GET /health` exposes per-provider status.

Register a new provider: implement `MediaProvider`, add to `build_providers()`,
set its position via `PROVIDER_ORDER`. No route or bot changes needed.

## 14. Provider fallback

`app/services/resolver.py` (`MediaResolver`) is the only place with fallback:

- Each operation iterates ordered **enabled** providers.
- Recoverable (`ProviderUnavailable/Timeout/BadResponse`) → log + try next.
- `ProviderNotFound` on info/stream/download → try next (the id may belong to
  another provider); if *all* say not-found → `404`.
- `ProviderInvalidQueryError` (bad user input) → `400` immediately, no fan-out.
- Zero enabled providers → `503/502 PROVIDER_UNAVAILABLE`, never a crash.

IDs may carry a hint (`provider_a:xyz`) — the hinted provider is tried first.

## 15. Telegram integration

Bot config (only these two values):

```env
MEDIA_API_URL=https://api.YOUR_DOMAIN.com
MEDIA_API_KEY=mapi_xxxxxxxxxxxxxxxxx
```

```text
Telegram Bot ── X-API-Key ─▶ MEDIA_API_URL ─▶ Media API ─▶ Provider Resolver
```

Minimal Python example (standard library + httpx):

```python
import os, httpx

BASE = os.environ["MEDIA_API_URL"]          # e.g. https://api.YOUR_DOMAIN.com
KEY = os.environ["MEDIA_API_KEY"]           # mapi_...
H = {"X-API-Key": KEY}

hits = httpx.get(f"{BASE}/v1/search", params={"q": "Raiden Shogun"}, headers=H).json()
media_id = hits["data"]["results"][0]["id"]
stream = httpx.get(f"{BASE}/v1/stream/{media_id}", headers=H).json()
audio_url = stream["data"]["url"]  # send to Telegram with sendAudio
```

The bot never holds provider credentials and never talks to upstream providers
directly. Give each bot its own key (section 11) so keys are independently
revocable and rate-limited.

## 16. Testing

```bash
pytest            # 34 tests, all mocked — no live third-party calls
```

Coverage: health (no-auth, provider-down resilience), auth (missing/invalid/
valid/revoked/expired, admin isolation, masked listing), permissions
(allowed/denied scope), rate limits (below/above, health exempt), API
(search/info/stream/download + error envelope), providers (success, disabled,
timeout, malformed), fallback (next-provider retry, no fan-out on invalid
query, empty-registry handling).

## 17. Docker

```bash
docker build -t media-api .
docker run -e PORT=8000 -e MASTER_API_SECRET=... -p 8000:8000 media-api
```

Or full local stack (API + Postgres + Redis):

```bash
cp .env.example .env   # fill secrets
docker compose up --build
```

## 18. Railway deployment

1. Push this repo, create a Railway service from it (Dockerfile is detected).
2. Add Postgres + Redis plugins; set `DATABASE_URL`, `REDIS_URL`.
3. Set `MASTER_API_SECRET`, `API_BASE_URL=https://api.YOUR_DOMAIN.com`,
   `APP_ENV=production`, `MOCK_PROVIDER_ENABLED=false`, `CORS_ORIGINS`.
4. Railway injects `PORT` — the container already honors `${PORT:-8000}`.
5. Health check path: `/health`. Run `alembic upgrade head` once
   (or against the Railway DB URL) — fresh DBs also self-initialize tables.

No Railway-specific logic is hard-coded; everything is env-driven.

## 19. Custom domain setup

The app does not care which registrar/DNS provider owns the domain:

1. Deploy (Railway/VPS) and get the service's public endpoint.
2. Follow your host's custom-domain instructions to point the `api` subdomain
   (e.g. a `CNAME api → <railway-domain>`) at the deployment and enable HTTPS.
3. Set `API_BASE_URL=https://api.YOUR_DOMAIN.com` so docs/links use the domain.
4. Verify: `curl https://api.YOUR_DOMAIN.com/health` and update bots'
   `MEDIA_API_URL`.

## 20. Security

- Keys: crypto-random, high entropy, SHA-256 hashed, masked in listings,
  plaintext shown once; admin auth is a separate secret (`X-Admin-Key`).
- Request validation (Pydantic + query bounds), per-key rate limits (429),
  upstream timeouts, secret redaction in logs, configurable CORS
  (no `*` in production), structured errors without stack traces in production,
  request IDs for traceability.
- Never commit `.env` (gitignored); `.env.example` holds placeholders only.

## 21. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/health` → `degraded`, `database: down` | `DATABASE_URL` wrong/unreachable; check connectivity, run `alembic upgrade head` |
| `503 PROVIDER_UNAVAILABLE` on `/v1/*` | no enabled provider: set provider creds or enable mock (`MOCK_PROVIDER_ENABLED=true`); check `/health` → `providers` |
| `401 INVALID_API_KEY` | wrong key / wrong environment; create a fresh key via `/admin/keys` |
| `403 FORBIDDEN_SCOPE` | key lacks the endpoint's scope; `PATCH /admin/keys/{id}` to add it |
| `429 RATE_LIMITED` | per-key per-minute limit hit; raise `rate_limit` or back off (`Retry` after `reset` window) |
| Admin `503 ADMIN_DISABLED` | `MASTER_API_SECRET` empty |
| CORS errors in browsers | set `CORS_ORIGINS` to the exact web origins |
| Tests fail on `DATABASE_URL` | tests set their own sqlite DB; don't export a conflicting `DATABASE_URL` shell-wide when running `pytest` |
