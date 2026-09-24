"""Provider B integration point."""

from __future__ import annotations

from app.providers.http_base import HttpProviderBase


class ProviderB(HttpProviderBase):
    name = "provider_b"
