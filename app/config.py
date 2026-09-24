"""Central configuration loaded from environment variables.

No secrets are hard-coded. The production domain is configured via
API_BASE_URL; provider credentials are optional at boot time.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field(default="development")
    app_name: str = Field(default="Media API")
    app_version: str = Field(default="1.0.0")
    api_base_url: str = Field(default="https://api.YOUR_DOMAIN.com")

    database_url: str = Field(default="")
    redis_url: str = Field(default="")

    master_api_secret: str = Field(default="")

    cors_origins: str = Field(default="")
    log_level: str = Field(default="INFO")

    provider_order: str = Field(default="provider_a,provider_b,provider_c")
    mock_provider_enabled: bool = Field(default=True)
    provider_timeout_seconds: float = Field(default=10.0)

    provider_a_api_key: str = Field(default="")
    provider_a_base_url: str = Field(default="")
    provider_b_api_key: str = Field(default="")
    provider_b_base_url: str = Field(default="")
    provider_c_api_key: str = Field(default="")
    provider_c_base_url: str = Field(default="")

    rate_limit_per_minute: int = Field(default=60)
    cache_ttl_search_seconds: int = Field(default=300)
    cache_ttl_info_seconds: int = Field(default=600)

    port: int = Field(default=8000)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return ["*"] if not self.is_production else []
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def provider_order_list(self) -> list[str]:
        return [p.strip().lower() for p in (self.provider_order or "").split(",") if p.strip()]

    def provider_api_key(self, name: str) -> str:
        return {
            "provider_a": self.provider_a_api_key,
            "provider_b": self.provider_b_api_key,
            "provider_c": self.provider_c_api_key,
        }.get(name.lower(), "")

    def provider_base_url(self, name: str) -> str:
        return {
            "provider_a": self.provider_a_base_url,
            "provider_b": self.provider_b_base_url,
            "provider_c": self.provider_c_base_url,
        }.get(name.lower(), "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
