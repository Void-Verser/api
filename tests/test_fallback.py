"""Resolver: capability-aware fallback, no fan-out on bad input, no substitution."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.base import (
    DOWNLOAD,
    INFO,
    SEARCH,
    STREAM,
    MediaProvider,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
)
from app.providers.manager import ProviderManager
from app.providers.mock import FailingProvider, MockProvider
from app.services.cache import CacheService
from app.services.resolver import (
    MediaResolver,
    ResolverBadRequest,
    ResolverNotFound,
    ResolverUnavailable,
    ResolverUnsupported,
    route_media_id,
)

ALL = frozenset({SEARCH, INFO, STREAM, DOWNLOAD})


def _resolver(order: str, extra: dict, **over):
    s = Settings(_env_file=None, provider_order=order, mock_provider_enabled=False,
                 youtube_search_backend="off", app_env="test", **over)
    m = ProviderManager(s, extra=extra)
    return MediaResolver(m, CacheService(), s)


class SearchNotFound(FailingProvider):
    """Upstream with no search endpoint: every search 404s."""

    name = "notfound"

    async def search(self, query, limit=10, offset=0):
        raise ProviderNotFoundError("Media not found via notfound")


@pytest.mark.asyncio
async def test_fallback_to_next_provider_on_failure():
    r = _resolver("failing,mock", {"failing": FailingProvider(), "mock": MockProvider()})
    results, provider = await r.search("hello", limit=2)
    assert provider == "mock"
    assert len(results) == 2


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
        capabilities = ALL

        async def search(self, query, limit=10, offset=0):
            raise ProviderInvalidQueryError("bad query")

    class ShouldNotRun(MediaProvider):
        name = "never"
        capabilities = ALL

        async def search(self, query, limit=10, offset=0):
            raise AssertionError("must not fan out after invalid query")

    r = _resolver("badq,never", {"badq": BadQuery(), "never": ShouldNotRun()})
    with pytest.raises(ResolverBadRequest):
        await r.search("hello")


@pytest.mark.asyncio
async def test_unavailable_when_no_search_provider_enabled():
    r = _resolver("youtube", {})  # youtube search off, others unconfigured
    with pytest.raises(ResolverUnavailable, match="media provider"):
        await r.search("hello")


@pytest.mark.asyncio
async def test_info_falls_back_across_providers_for_unprefixed_ids():
    r = _resolver("failing,mock", {"failing": FailingProvider("down"), "mock": MockProvider()})
    info, provider = await r.get_info("mock-some-id-1")
    assert provider == "mock"
    assert info["id"] == "mock-some-id-1"


@pytest.mark.asyncio
async def test_prefixed_id_is_never_served_by_another_provider():
    # Spotify is unconfigured; the mock must NOT answer for a spotify: id.
    r = _resolver("mock", {"mock": MockProvider()})
    with pytest.raises(ResolverUnavailable, match="spotify is not configured"):
        await r.get_info("spotify:4cOdK2wGLETKBW3PvgPWqT")


@pytest.mark.asyncio
async def test_metadata_is_not_reported_as_stream():
    # YouTube supports metadata but stream extraction is disabled -> 422, no fallback.
    r = _resolver("youtube,mock", {"mock": MockProvider()})
    with pytest.raises(ResolverUnsupported):
        await r.get_stream("youtube:dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_explicit_provider_search_does_not_fall_back():
    r = _resolver("mock", {"mock": MockProvider()})
    with pytest.raises(ResolverUnavailable):
        await r.search("hello", provider="spotify")


@pytest.mark.asyncio
async def test_unknown_unprefixed_id_is_404():
    r = _resolver("mock", {"mock": MockProvider()})
    with pytest.raises(ResolverNotFound):
        await r.get_info("definitely-unknown")


def test_route_media_id():
    assert route_media_id("dQw4w9WgXcQ") == ("youtube", "youtube:dQw4w9WgXcQ")
    assert route_media_id("spotify:track:4cOdK2wGLETKBW3PvgPWqT")[0] == "spotify"
    assert route_media_id("mock-x") == (None, "mock-x")
    with pytest.raises(ResolverBadRequest):
        route_media_id("evil:thing")


@pytest.mark.asyncio
async def test_stream_unavailable_maps_to_503_in_route(client, make_key):
    from app import main as main_module
    from app.config import get_settings
    from app.dependencies import get_resolver
    from app.providers.manager import ProviderManager as PM
    from app.services.cache import CacheService as CS
    from app.services.resolver import MediaResolver as MR

    settings = get_settings()
    plain, _ = make_key()

    class EmptyManager(PM):
        def get_ordered_providers(self):
            return []

    main_module.app.dependency_overrides[get_resolver] = lambda: MR(EmptyManager(settings), CS(), settings)
    try:
        r = client.get("/v1/stream/anything", headers={"X-API-Key": plain})
        assert r.status_code in (502, 503)
        assert r.json()["success"] is False
    finally:
        main_module.app.dependency_overrides.pop(get_resolver, None)
