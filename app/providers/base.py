"""Provider interface + error taxonomy.

Routes must NEVER contain provider-specific logic. They talk to the
resolver, which talks to the manager, which talks to MediaProviders.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(Exception):
    """Base upstream failure. `recoverable=True` means try the next provider."""

    recoverable: bool = True
    code: str = "PROVIDER_ERROR"

    def __init__(self, message: str = "Provider error"):
        super().__init__(message)


class ProviderUnavailableError(ProviderError):
    code = "PROVIDER_UNAVAILABLE"


class ProviderTimeoutError(ProviderError):
    code = "PROVIDER_TIMEOUT"


class ProviderNotFoundError(ProviderError):
    """Media not found on this provider. Resolver may still try others."""

    recoverable = False
    code = "NOT_FOUND"


class ProviderBadResponseError(ProviderError):
    """Malformed upstream response — worth trying the next provider."""

    recoverable = True
    code = "PROVIDER_BAD_RESPONSE"


class ProviderInvalidQueryError(ProviderError):
    """Invalid user request — must NOT fan out to every provider."""

    recoverable = False
    code = "INVALID_QUERY"


@dataclass
class ProviderStatus:
    name: str
    enabled: bool
    reason: str = ""


class MediaProvider(ABC):
    """All providers implement this interface and return NORMALIZED dicts."""

    name: str = "base"

    @property
    @abstractmethod
    def enabled(self) -> bool:
        ...

    @abstractmethod
    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        """Return list of normalized media dicts (see MediaResult schema)."""
        ...

    @abstractmethod
    async def get_info(self, media_id: str) -> dict:
        ...

    @abstractmethod
    async def get_stream(self, media_id: str) -> dict:
        ...

    @abstractmethod
    async def get_download(self, media_id: str) -> dict:
        ...

    def status(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, enabled=self.enabled)
