"""Central configuration loaded from environment variables.

No secrets or deployment URLs are hard-coded. Every optional integration
may be left blank; the API still boots and reports what is missing.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

KNOWN_PROVIDERS = ("youtube", "soundcloud", "spotify", "apple_music", "mock")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---------------------------------------------------------------
    app_env: str = Field(default="development")
    app_name: str = Field(default="Media API")
    # Public base URL. Empty -> auto-detected (Vercel system env, then request).
    api_base_url: str = Field(default="")
    log_level: str = Field(default="INFO")
    cors_origins: str = Field(default="")
    port: int = Field(default=8000)

    # --- Persistence -------------------------------------------------------
    database_url: str = Field(default="")
    redis_url: str = Field(default="")
    # Run alembic migrations on startup (safe/idempotent). Disable if you
    # prefer running `alembic upgrade head` as a separate release step.
    run_migrations_on_startup: bool = Field(default=True)

    # --- Owner / admin auth --------------------------------------------------
    # Server-side automation secret for /admin/* (X-Admin-Key). Never sent to
    # the browser. Optional if you only use the dashboard.
    master_api_secret: str = Field(default="")
    # Owner bootstrap: created once on startup if no owner exists yet.
    owner_email: str = Field(default="")
    owner_password: str = Field(default="")
    session_ttl_hours: int = Field(default=12, ge=1, le=24 * 30)
    # Secret for signing result-file links. Falls back to a value derived
    # from MASTER_API_SECRET; set explicitly in production.
    signing_secret: str = Field(default="")

    # --- Providers -----------------------------------------------------------
    provider_order: str = Field(default="youtube,soundcloud,spotify,apple_music")
    mock_provider_enabled: bool = Field(default=False)
    provider_timeout_seconds: float = Field(default=10.0)

    youtube_api_key: str = Field(default="")
    # auto: Data API when YOUTUBE_API_KEY is set, else yt-dlp search.
    youtube_search_backend: str = Field(default="auto")
    # yt-dlp based stream/download URL extraction and conversion jobs. Off by
    # default: enable only for content you are authorized to retrieve.
    media_extraction_enabled: bool = Field(default=False)
    ytdlp_cookies_file: str = Field(default="")
    ytdlp_proxy: str = Field(default="")

    spotify_client_id: str = Field(default="")
    spotify_client_secret: str = Field(default="")
    spotify_market: str = Field(default="US")

    apple_music_developer_token: str = Field(default="")
    apple_music_team_id: str = Field(default="")
    apple_music_key_id: str = Field(default="")
    apple_music_private_key: str = Field(default="")
    apple_music_storefront: str = Field(default="us")

    soundcloud_client_id: str = Field(default="")
    soundcloud_client_secret: str = Field(default="")

    # --- Abuse protection (configurable; no subscription quotas) ------------
    rate_limit_per_minute: int = Field(default=120)
    login_attempts_per_minute: int = Field(default=10)
    max_active_jobs_per_key: int = Field(default=5)

    cache_ttl_search_seconds: int = Field(default=300)
    cache_ttl_info_seconds: int = Field(default=600)

    # --- Media jobs ------------------------------------------------------------
    job_max_media_duration_seconds: int = Field(default=1800)
    job_max_output_mb: int = Field(default=300)
    job_timeout_seconds: int = Field(default=900)
    job_max_attempts: int = Field(default=2)
    job_lease_seconds: int = Field(default=1200)
    max_video_height: int = Field(default=720)
    result_retention_hours: int = Field(default=24)
    result_link_ttl_seconds: int = Field(default=3600)
    worker_poll_seconds: float = Field(default=2.0)
    # Run a worker thread inside the API process (single-host deployments
    # only; never on serverless).
    worker_embedded: bool = Field(default=False)
    ffmpeg_path: str = Field(default="")

    # --- Storage -----------------------------------------------------------------
    storage_backend: str = Field(default="local")  # local | s3
    local_storage_dir: str = Field(default="./data/results")
    s3_bucket: str = Field(default="")
    s3_region: str = Field(default="auto")
    s3_endpoint_url: str = Field(default="")
    s3_access_key_id: str = Field(default="")
    s3_secret_access_key: str = Field(default="")

    # ------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_test(self) -> bool:
        return self.app_env.lower() == "test"

    @property
    def on_vercel(self) -> bool:
        return bool(os.getenv("VERCEL"))

    @property
    def mock_enabled(self) -> bool:
        # Fake results are never served in production.
        return self.mock_provider_enabled and not self.is_production

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return ["*"] if not self.is_production else []
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def provider_order_list(self) -> list[str]:
        return [p.strip().lower() for p in (self.provider_order or "").split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
