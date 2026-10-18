# AGENTS.md — instructions for coding agents

**Project:** Media API — https://github.com/Void-Verser/api
**Purpose:** YouTube-first media API for Telegram bots and other apps: search,
metadata, URL/Shorts resolution and MP3/MP4 conversion jobs behind one client
API key per bot, plus an owner dashboard to issue and revoke keys.

## Before you change anything

1. Read this file, then **`docs/agent-handoff.md`** (canonical current state
   and exact next steps), `docs/architecture.md`, and `README.md`.
2. Run `git status`, `git log --oneline -10`, `git remote -v`. Preserve any
   uncommitted work; never reset, clean or force-push.
3. Compare the handoff with the code. If they disagree, the code and fresh
   test results win — fix the handoff.
4. Continue from the first unfinished item in the handoff instead of redoing
   the audit.

## Non-negotiable constraints

- **One client key per bot** works for every client operation (`["*"]` by
  default). Keys are stored only as hashes and shown once.
- **YouTube is the primary provider**; verify YouTube behavior first.
- **Honest capabilities:** metadata is not a stream, a link is not a download.
  Spotify/Apple Music are catalog-only. Never substitute content from another
  catalog silently. Never circumvent DRM or access controls.
- **No third-party media API as a runtime dependency.** A third-party reference
  service was supplied by the owner for one-off diagnostics only. Do not add
  its URL or credentials to code, config, tests, docs, commits or logs. If you
  lack diagnostic access, use official docs — do not search history for keys.
- **Secrets:** never commit `.env` or real credentials; `.env.example` holds
  placeholders only. Never print secrets in output.
- **Mock provider** is dev/test only and is forced off when `APP_ENV=production`.
- **Portability:** Vercel is the preferred API host but must not become a hard
  dependency; workers run on any Docker host; DB/queue/storage stay configurable.
- Keep routes provider-agnostic (logic lives in `app/services/resolver.py` and
  `app/providers/*`). Schema changes need an Alembic migration in `migrations/versions/`.

## Workflow

- Tests: `pip install -r requirements-dev.txt && pytest` (no live network;
  a fixture blocks it). Add tests with every change.
- Live checks against real services are manual, low-volume, and must be
  recorded in the handoff as "live", separately from mocked tests.
- **Before stopping:** update `docs/agent-handoff.md` (status, files changed,
  test results, remaining tasks, exact next steps, git/deploy state).
- Commit with a meaningful message and push to `main` when authorized and
  possible; verify the remote contains the commit. Report deployment status
  separately from the push.
