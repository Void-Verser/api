"""Central resolver: capability-aware provider selection + fallback.

API routes call the resolver; the resolver owns all fallback logic so
routes stay provider-agnostic.

* Search walks the ordered, enabled, search-capable providers (YouTube
  first by default). Recoverable errors and "not found" fall through.
* IDs carry their provider (`youtube:ID`, `spotify:ID`, ...). A prefixed ID
  is only ever served by that provider — we never silently substitute
  content from a different catalog. Bare 11-char IDs are YouTube IDs.
* Operations are only routed to providers that declare the capability:
  metadata is never reported as a stream, a link never as a download.
"""

from __future__ import annotations

import logging

from app.config import KNOWN_PROVIDERS, Settings
from app.providers.base import (
    DOWNLOAD,
    INFO,
    SEARCH,
    STREAM,
    MediaRestrictedError,
    ProviderError,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
    ProviderUnsupportedError,
)
from app.providers.manager import ProviderManager
from app.providers.youtube import VIDEO_ID_RE
from app.services.cache import CacheService

log = logging.getLogger("media_api.resolver")


class ResolverError(Exception):
    status = 500
    code = "RESOLVER_ERROR"


class ResolverNotFound(ResolverError):
    status, code = 404, "NOT_FOUND"


class ResolverUnavailable(ResolverError):
    status, code = 503, "PROVIDER_UNAVAILABLE"


class ResolverBadRequest(ResolverError):
    status, code = 400, "BAD_REQUEST"


class ResolverUnsupported(ResolverError):
    status, code = 422, "UNSUPPORTED_OPERATION"


class ResolverRestricted(ResolverError):
    status, code = 403, "MEDIA_RESTRICTED"


def route_media_id(media_id: str) -> tuple[str | None, str]:
    """Return (provider|None, id). Bare 11-char ids are YouTube ids."""
    media_id = (media_id or "").strip()
    if ":" in media_id:
        hint, rest = media_id.split(":", 1)
        hint = hint.strip().lower()
        if hint in KNOWN_PROVIDERS and rest:
            return hint, media_id
        if hint and rest:
            raise ResolverBadRequest(f"Unknown provider prefix '{hint}'.")
    if VIDEO_ID_RE.match(media_id):
        return "youtube", f"youtube:{media_id}"
    return None, media_id


class MediaResolver:
    def __init__(self, manager: ProviderManager, cache: CacheService, settings: Settings):
        self.manager = manager
        self.cache = cache
        self.settings = settings

    # -- selection ----------------------------------------------------------
    def _named(self, name: str, capability: str):
        provider = self.manager.get(name)
        if provider is None:
            raise ResolverNotFound(f"Provider '{name}' is not available on this server.")
        if not provider.enabled:
            raise ResolverUnavailable(f"{name} is not configured: {provider.disabled_reason()}")
        if not provider.supports(capability):
            raise ResolverUnsupported(_unsupported_message(name, capability, provider))
        return provider

    def _candidates(self, capability: str):
        return [p for p in self.manager.get_ordered_providers() if p.enabled and p.supports(capability)]

    @staticmethod
    def _translate(exc: ProviderError) -> ResolverError:
        if isinstance(exc, ProviderInvalidQueryError):
            return ResolverBadRequest(str(exc))
        if isinstance(exc, ProviderNotFoundError):
            return ResolverNotFound(str(exc))
        if isinstance(exc, MediaRestrictedError):
            return ResolverRestricted(str(exc))
        if isinstance(exc, ProviderUnsupportedError):
            return ResolverUnsupported(str(exc))
        return ResolverUnavailable(str(exc))

    # -- search ---------------------------------------------------------------
    async def search(
        self, query: str, limit: int = 10, offset: int = 0, provider: str | None = None
    ) -> tuple[list[dict], str | None]:
        if not query or not query.strip():
            raise ResolverBadRequest("Query must not be empty")
        query = query.strip()
        cache_key = f"search:{provider or '*'}:{query.lower()}:{limit}:{offset}"
        cached = await self.cache.get_json(cache_key)
        if cached is not None:
            return cached.get("results", []), cached.get("provider")

        providers = [self._named(provider, SEARCH)] if provider else self._candidates(SEARCH)
        if not providers:
            raise ResolverUnavailable("No search-capable media provider is configured.")
        last_error: Exception | None = None
        for p in providers:
            try:
                results = await p.search(query, limit=limit, offset=offset) or []
            except ProviderInvalidQueryError as exc:
                raise ResolverBadRequest(str(exc)) from exc
            except (ProviderNotFoundError, ProviderUnsupportedError) as exc:
                # This provider can't serve the query; not proof nothing exists.
                last_error = exc
                log.info("provider %s search skipped: %s", p.name, exc)
                continue
            except ProviderError as exc:
                last_error = exc
                if not exc.recoverable:
                    raise self._translate(exc) from exc
                log.info("provider %s search failed, falling back: %s", p.name, exc)
                continue
            await self.cache.set_json(
                cache_key, {"results": results, "provider": p.name},
                ttl=self.settings.cache_ttl_search_seconds,
            )
            return results, p.name
        if provider and last_error is not None:
            raise self._translate(last_error)
        raise ResolverUnavailable(str(last_error) if last_error else "All providers failed.")

    # -- id-based operations -----------------------------------------------
    async def _by_id(self, capability: str, media_id: str) -> tuple[dict, str]:
        name, routed_id = route_media_id(media_id)
        if name:
            provider = self._named(name, capability)
            try:
                return await self._call(provider, capability, routed_id), provider.name
            except ProviderError as exc:
                raise self._translate(exc) from exc

        # Un-prefixed legacy ids: ask each capable provider; never fan out
        # on invalid input.
        providers = self._candidates(capability)
        if not providers:
            raise ResolverUnavailable("No media provider is currently available.")
        last_error: ProviderError | None = None
        not_found_only = True
        for provider in providers:
            try:
                return await self._call(provider, capability, routed_id), provider.name
            except ProviderInvalidQueryError:
                last_error = ProviderNotFoundError(f"Media not found: {media_id}")
                continue
            except ProviderNotFoundError as exc:
                last_error = exc
                continue
            except ProviderError as exc:
                last_error = exc
                not_found_only = False
                if not exc.recoverable:
                    raise self._translate(exc) from exc
                log.info("provider %s %s failed, falling back: %s", provider.name, capability, exc)
        if not_found_only:
            raise ResolverNotFound(f"Media not found: {media_id}")
        raise ResolverUnavailable(str(last_error) if last_error else "All providers failed.")

    @staticmethod
    async def _call(provider, capability: str, media_id: str) -> dict:
        if capability == INFO:
            return await provider.get_info(media_id)
        if capability == STREAM:
            return await provider.get_stream(media_id)
        if capability == DOWNLOAD:
            return await provider.get_download(media_id)
        raise ResolverUnsupported(f"Unknown operation {capability}")

    async def get_info(self, media_id: str) -> tuple[dict, str]:
        cache_key = f"info:{media_id}"
        cached = await self.cache.get_json(cache_key)
        if cached is not None:
            return cached, cached.get("provider", "unknown")
        info, provider = await self._by_id(INFO, media_id)
        await self.cache.set_json(cache_key, info, ttl=self.settings.cache_ttl_info_seconds)
        return info, provider

    async def get_stream(self, media_id: str) -> tuple[dict, str]:
        return await self._by_id(STREAM, media_id)

    async def get_download(self, media_id: str) -> tuple[dict, str]:
        return await self._by_id(DOWNLOAD, media_id)


def _unsupported_message(name: str, capability: str, provider) -> str:
    if name in ("spotify", "apple_music") and capability in (STREAM, DOWNLOAD):
        return (f"{name} provides catalog metadata and official links only; it does not "
                f"permit audio {capability}. Use /v1/resolve with match_youtube=true to find "
                "a YouTube candidate explicitly.")
    status = provider.status()
    extra = f" ({status.reason})" if status.reason else ""
    return f"{name} does not support {capability} on this server{extra}."
