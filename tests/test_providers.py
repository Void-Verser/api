"""Provider unit tests: success, failure, timeout, malformed (all mocked)."""

from __future__ import annotations

import pytest

from app.providers.base import (
    ProviderBadResponseError,
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.manager import ProviderManager
from app.providers.mock import FailingProvider, MockProvider
from app.providers.provider_a import ProviderA
from app.config import Settings


def _settings(**over):
    base = dict(
        provider_order="provider_a,provider_b,provider_c",
        mock_provider_enabled=True,
        provider_timeout_seconds=1.0,
    )
    base.update(over)
    return Settings(**base)


async def _run(coro):
    return await coro


@pytest.mark.asyncio
async def test_mock_search_success():
    p = MockProvider()
    results = await p.search("Raiden Shogun", limit=2)
    assert len(results) == 2
    assert results[0]["provider"] == "mock"
    assert "Raiden Shogun" in results[0]["title"]


@pytest.mark.asyncio
async def test_unconfigured_provider_reports_cleanly():
    p = ProviderA(api_key="", base_url="")
    assert p.enabled is False
    with pytest.raises(ProviderUnavailableError, match="credentials not configured"):
        await p.search("x")


@pytest.mark.asyncio
async def test_failing_provider_raises_recoverable():
    p = FailingProvider()
    with pytest.raises(ProviderUnavailableError):
        await p.search("x")


@pytest.mark.asyncio
async def test_timeout_is_recoverable():
    assert ProviderTimeoutError("t").recoverable is True
    assert ProviderNotFoundError("nf").recoverable is False


@pytest.mark.asyncio
async def test_malformed_response_error_shape():
    err = ProviderBadResponseError("bad json")
    assert err.recoverable is True
    assert err.code == "PROVIDER_BAD_RESPONSE"


def test_manager_order_configurable():
    s = _settings(provider_order="mock,provider_a")
    m = ProviderManager(s)
    assert m.order[0] == "mock"


def test_manager_status_lists_all():
    m = ProviderManager(_settings())
    names = [s.name for s in m.status()]
    assert {"provider_a", "provider_b", "provider_c", "mock"} <= set(names)
