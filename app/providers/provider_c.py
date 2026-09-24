"""Provider C integration point."""

from __future__ import annotations

from app.providers.http_base import HttpProviderBase


class ProviderC(HttpProviderBase):
    name = "provider_c"
