"""Central resolver: provider selection + fallback + normalization.

API routes call the resolver; the resolver owns all fallback logic so
routes stay provider-agnostic. Recoverable upstream errors (timeout,
5xx, malformed response, unavailable) fall through to the next provider.
Non-recoverable errors (invalid query) abort immediately.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.providers.base import (
    ProviderError,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
)
from app.providers.manager import ProviderManager
from app.services.cache import CacheService
from app.services.media import split_media_id

log = logging.getLogger("media_api.resolver")


class ResolverNotFound(Exception):
    pass


class ResolverUnavailable(Exception):
    pass


class ResolverBadRequest(Exception):
    pass


class MediaResolver:
    def __init__(self, manager: ProviderManager, cache: CacheService, settings: Settings):
        self.manager = manager
        self.cache = cache
        self.settings = settings

    def _ordered(self, media_id: str | None = None):
        providers = self.manager.get_ordered_providers()
        if not media_id:
            return providers, None
        hint, _ = split_media_id(media_id)
        if hint:
            preferred = [p for p in providers if p.name == hint]
            rest = [p for p in providers if p.name != hint]
            if preferred:
                return preferred + rest, hint
        return providers, hint

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> tuple[list[dict], str | None]:
        if not query or not query.strip():
            raise ResolverBadRequest("Query must not be empty")
        cache_key = f"search:{query.strip().lower()}:{limit}:{offset}"
        cached = await self.cache.get_json(cache_key)
        if cached is not None:
            return cached.get("results", []), cached.get("provider")

        last_error: Exception | None = None
        attempted = 0
        for provider in self.manager.get_ordered_providers():
            if not provider.enabled:
                continue
            attempted += 1
            try:
                results = await provider.search(query.strip(), limit=limit, offset=offset)
                results = results or []
                await self.cache.set_json(
                    cache_key,
                    {"results": results, "provider": provider.name},
                    ttl=self.settings.cache_ttl_search_seconds,
                )
                return results, provider.name
            except ProviderInvalidQueryError as exc:
                raise ResolverBadRequest(str(exc)) from exc
            except ProviderNotFoundError as exc:
                # A 404 on search means this provider can't serve the query
                # (e.g. no search endpoint) — not that media doesn't exist.
                last_error = exc
                log.info("provider %s search not found, falling back: %s", provider.name, exc)
                continue
            except ProviderError as exc:
                # Recoverable -> try next; other non-recoverable errors abort.
                last_error = exc
                if not exc.recoverable:
                    raise ResolverNotFound(str(exc)) from exc
                log.info("provider %s search failed, falling back: %s", provider.name, exc)
                continue
        if attempted == 0:
            raise ResolverUnavailable("No media provider is currently available.")
        raise ResolverUnavailable(str(last_error) if last_error else "All providers failed.")

    async def get_info(self, media_id: str) -> tuple[dict, str]:
        cache_key = f"info:{media_id}"
        cached = await self.cache.get_json(cache_key)
        if cached is not None:
            return cached, cached.get("provider", "unknown")

        providers, _ = self._ordered(media_id)
        last_error: Exception | None = None
        attempted = 0
        not_found: Exception | None = None
        for provider in providers:
            if not provider.enabled:
                continue
            attempted += 1
            try:
                info = await provider.get_info(media_id)
                await self.cache.set_json(
                    cache_key, info, ttl=self.settings.cache_ttl_info_seconds
                )
                return info, provider.name
            except ProviderNotFoundError as exc:
                not_found = exc  # try next provider; id may belong elsewhere
                last_error = exc
                continue
            except ProviderInvalidQueryError as exc:
                raise ResolverBadRequest(str(exc)) from exc
            except ProviderError as exc:
                last_error = exc
                if not exc.recoverable:
                    raise ResolverNotFound(str(exc)) from exc
                log.info("provider %s info failed, falling back: %s", provider.name, exc)
                continue
        if attempted == 0:
            raise ResolverUnavailable("No media provider is currently available.")
        if not_found is not None and (last_error is not_found):
            # Every attempted provider said "not found".
            raise ResolverNotFound(str(not_found))
        raise ResolverUnavailable(str(last_error) if last_error else "All providers failed.")

    async def _resolve_url(self, kind: str, media_id: str) -> tuple[dict, str]:
        providers, _ = self._ordered(media_id)
        last_error: Exception | None = None
        attempted = 0
        not_found: Exception | None = None
        for provider in providers:
            if not provider.enabled:
                continue
            attempted += 1
            try:
                fn = provider.get_stream if kind == "stream" else provider.get_download
                data = await fn(media_id)
                return data, provider.name
            except ProviderNotFoundError as exc:
                not_found = exc
                last_error = exc
                continue
            except ProviderInvalidQueryError as exc:
                raise ResolverBadRequest(str(exc)) from exc
            except ProviderError as exc:
                last_error = exc
                if not exc.recoverable:
                    raise ResolverNotFound(str(exc)) from exc
                log.info("provider %s %s failed, falling back: %s", provider.name, kind, exc)
                continue
        if attempted == 0:
            raise ResolverUnavailable("No media provider is currently available.")
        if not_found is not None and last_error is not_found:
            raise ResolverNotFound(str(not_found))
        raise ResolverUnavailable(str(last_error) if last_error else "All providers failed.")

    async def get_stream(self, media_id: str) -> tuple[dict, str]:
        return await self._resolve_url("stream", media_id)

    async def get_download(self, media_id: str) -> tuple[dict, str]:
        return await self._resolve_url("download", media_id)
