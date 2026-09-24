"""Provider registry. Order is configurable via PROVIDER_ORDER."""

from __future__ import annotations

from app.config import Settings
from app.providers.base import MediaProvider, ProviderStatus
from app.providers.mock import MockProvider
from app.providers.provider_a import ProviderA
from app.providers.provider_b import ProviderB
from app.providers.provider_c import ProviderC


def build_providers(settings: Settings) -> dict[str, MediaProvider]:
    timeout = settings.provider_timeout_seconds
    return {
        "provider_a": ProviderA(
            api_key=settings.provider_a_api_key,
            base_url=settings.provider_a_base_url,
            timeout=timeout,
        ),
        "provider_b": ProviderB(
            api_key=settings.provider_b_api_key,
            base_url=settings.provider_b_base_url,
            timeout=timeout,
        ),
        "provider_c": ProviderC(
            api_key=settings.provider_c_api_key,
            base_url=settings.provider_c_base_url,
            timeout=timeout,
        ),
        "mock": MockProvider(enabled=settings.mock_provider_enabled),
    }


class ProviderManager:
    def __init__(self, settings: Settings, extra: dict[str, MediaProvider] | None = None):
        self.settings = settings
        registry = build_providers(settings)
        if extra:
            registry.update(extra)
        self._registry = registry
        order = [p for p in settings.provider_order_list if p in registry]
        # Append mock last if enabled and not explicitly ordered.
        if settings.mock_provider_enabled and "mock" not in order and "mock" in registry:
            order.append("mock")
        # Append any remaining registered providers not in the order.
        for name in registry:
            if name not in order:
                order.append(name)
        self._order = order

    @property
    def order(self) -> list[str]:
        return list(self._order)

    def get_ordered_providers(self) -> list[MediaProvider]:
        return [self._registry[name] for name in self._order]

    def get(self, name: str) -> MediaProvider | None:
        return self._registry.get(name)

    def status(self) -> list[ProviderStatus]:
        return [self._registry[name].status() for name in self._order]
