"""Fallback: resolver tries next provider on recoverable errors only."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.base import (
    MediaProvider,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.providers.manager import ProviderManager
from app.providers.mock import FailingProvider, MockProvider
from app.services.cache import CacheService
from app.services.resolver import (
    MediaResolver,
    ResolverBadRequest,
    ResolverUnavailable,
)


def _resolver(order: str, extra: dict, **over):
    s = Settings(provider_order=order, mock_provider_enabled=False, **over)
    m = ProviderManager(s, extra=extra)
    return MediaResolver(m, CacheService(), s)


@pytest.mark.asyncio
async def test_fallback_to_next_provider_on_failure():
    r = _resolver("failing,mock", {"failing": FailingProvider(), "mock": MockProvider()})
    results, provider = await r.search("hello", limit=2)
    assert provider == "mock"
    assert len(results) == 2


class SearchNotFound(FailingProvider):
    """Upstream with no search endpoint: every search 404s."""

    name = "notfound"

    async def search(self, query, limit=10, offset=0):
        raise ProviderNotFoundError("Media not found via notfound")


@pytest.mark.asyncio
async def test_search_not_found_falls_back_to_next_provider():
    r = _resolver("notfound,mock", {"notfound": SearchNotFound(), "mock": MockProvider()})
    results, provider = await r.search("hello", limit=2)
    assert provider == "mock"
    assert len(results) == 2


@pytest.mark.asyncio
async def test_search_not_found_everywhere_is_unavailable_not_crash():
    r = _resolver("notfound", {"notfound": SearchNotFound()})
    with pytest.raises(ResolverUnavailable):
        await r.search("hello")


@pytest.mark.asyncio
async def test_no_blind_retry_on_invalid_query():
    class BadQuery(MediaProvider):
        name = "badq"

        @property
        def enabled(self):
            return True

        async def search(self, query, limit=10, offset=0):
            raise ProviderInvalidQueryError("bad query")

        async def get_info(self, media_id):
            raise ProviderInvalidQueryError("bad query")

        async def get_stream(self, media_id):
            raise ProviderInvalidQueryError("bad query")

        async def get_download(self, media_id):
            raise ProviderInvalidQueryError("bad query")

    class ShouldNotRun(MediaProvider):
        name = "never"

        @property
        def enabled(self):
            return True

        async def search(self, query, limit=10, offset=0):
            raise AssertionError("must not fan out after invalid query")

        async def get_info(self, media_id):
            raise AssertionError("must not fan out")

        async def get_stream(self, media_id):
            raise AssertionError("must not fan out")

        async def get_download(self, media_id):
            raise AssertionError("must not fan out")

    r = _resolver("badq,never", {"badq": BadQuery(), "never": ShouldNotRun()})
    with pytest.raises(ResolverBadRequest):
        await r.search("hello")


@pytest.mark.asyncio
async def test_unavailable_when_no_providers_enabled():
    r = _resolver("provider_a", {})
    # provider_a has no credentials -> disabled -> attempted == 0
    with pytest.raises(ResolverUnavailable, match="No media provider"):
        await r.search("hello")


@pytest.mark.asyncio
async def test_info_falls_back_across_providers():
    r = _resolver("failing,mock", {"failing": FailingProvider("down"), "mock": MockProvider()})
    info, provider = await r.get_info("mock-some-id-1")
    assert provider == "mock"
    assert info["id"] == "mock-some-id-1"


@pytest.mark.asyncio
async def test_stream_unavailable_maps_to_502_in_route(client, make_key):
    # End-to-end: with mock disabled there is nothing to serve -> 503/502,
    # proving routes surface provider outages instead of crashing.
    from app import main as main_module
    from app.config import get_settings
    from app.dependencies import get_resolver
    from app.providers.manager import ProviderManager as PM
    from app.services.cache import CacheService as CS
    from app.services.resolver import MediaResolver as MR

    settings = get_settings()
    plain, _ = make_key(permissions=["search", "info", "stream", "download"])

    class EmptyManager(PM):
        def get_ordered_providers(self):
            return []

    def _override():
        return MR(EmptyManager(settings), CS(), settings)

    main_module.app.dependency_overrides[get_resolver] = _override
    try:
        r = client.get("/v1/stream/anything", headers={"X-API-Key": plain})
        assert r.status_code in (502, 503)
        assert r.json()["success"] is False
    finally:
        main_module.app.dependency_overrides.pop(get_resolver, None)
