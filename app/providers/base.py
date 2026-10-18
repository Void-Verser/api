"""Provider interface + error taxonomy.

Routes must NEVER contain provider-specific logic. They talk to the
resolver, which talks to the manager, which talks to MediaProviders.

Each provider declares what it can genuinely do (`capabilities`). Metadata
is not a stream and an official link is not a download: the resolver only
routes an operation to providers that declare it.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field

SEARCH = "search"
INFO = "info"
STREAM = "stream"
DOWNLOAD = "download"
CONVERT = "convert"


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


class ProviderUnsupportedError(ProviderError):
    """The provider cannot perform this operation (by design or config)."""

    recoverable = True
    code = "UNSUPPORTED_OPERATION"


class MediaRestrictedError(ProviderError):
    """Private, age/region-restricted, DRM-protected or otherwise blocked."""

    recoverable = False
    code = "MEDIA_RESTRICTED"


@dataclass
class ProviderStatus:
    name: str
    enabled: bool
    reason: str = ""
    capabilities: list[str] = field(default_factory=list)


class MediaProvider(ABC):
    """All providers implement this interface and return NORMALIZED dicts.

    Normalized media dict keys: id, title, artist, album, duration (s),
    thumbnail, provider, media_type, url (official page), plus optional
    preview_url / isrc. `id` is always `<provider>:<native id>`.
    """

    name: str = "base"
    capabilities: frozenset[str] = frozenset()

    @property
    def enabled(self) -> bool:
        return True

    def disabled_reason(self) -> str:
        return ""

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def _unsupported(self, op: str):
        return ProviderUnsupportedError(f"{self.name} does not support {op}.")

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[dict]:
        raise self._unsupported("search")

    async def get_info(self, media_id: str) -> dict:
        raise self._unsupported("metadata lookup")

    async def get_stream(self, media_id: str) -> dict:
        raise self._unsupported("stream resolution")

    async def get_download(self, media_id: str) -> dict:
        raise self._unsupported("download resolution")

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            enabled=self.enabled,
            reason="" if self.enabled else self.disabled_reason(),
            capabilities=sorted(self.capabilities),
        )
