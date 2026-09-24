"""Provider A integration point."""

from __future__ import annotations

from app.providers.http_base import HttpProviderBase


class ProviderA(HttpProviderBase):
    name = "provider_a"
