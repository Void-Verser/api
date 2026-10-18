"""Provider registry. Order is configurable via PROVIDER_ORDER."""

from __future__ import annotations

from app.config import KNOWN_PROVIDERS, Settings
from app.providers.apple_music import AppleMusicProvider
from app.providers.base import MediaProvider, ProviderStatus
from app.providers.mock import MockProvider
from app.providers.soundcloud import SoundCloudProvider
from app.providers.spotify import SpotifyProvider
from app.providers.youtube import YouTubeProvider

DEFAULT_ORDER = ("youtube", "soundcloud", "spotify", "apple_music")


def build_providers(settings: Settings) -> dict[str, MediaProvider]:
    registry: dict[str, MediaProvider] = {
        "youtube": YouTubeProvider(settings),
        "soundcloud": SoundCloudProvider(settings),
        "spotify": SpotifyProvider(settings),
        "apple_music": AppleMusicProvider(settings),
    }
    if settings.mock_enabled:
        registry["mock"] = MockProvider(enabled=True)
    return registry


class ProviderManager:
    def __init__(self, settings: Settings, extra: dict[str, MediaProvider] | None = None):
        self.settings = settings
        registry = build_providers(settings)
        if extra:
            registry.update(extra)
        self._registry = registry
        order = [p for p in settings.provider_order_list if p in registry]
        # YouTube is the primary provider: unless explicitly ordered, it leads.
        if "youtube" in registry and "youtube" not in order:
            order.insert(0, "youtube")
        for name in [*DEFAULT_ORDER, *registry]:
            if name in registry and name not in order and name != "mock":
                order.append(name)
        # Mock (dev only) is last unless explicitly ordered.
        if "mock" in registry and "mock" not in order:
            order.append("mock")
        self._order = order

    @property
    def order(self) -> list[str]:
        return list(self._order)

    def get_ordered_providers(self) -> list[MediaProvider]:
        return [self._registry[name] for name in self._order]

    def get(self, name: str) -> MediaProvider | None:
        return self._registry.get(name)

    def is_known(self, name: str) -> bool:
        return name in self._registry or name in KNOWN_PROVIDERS

    def status(self) -> list[ProviderStatus]:
        return [self._registry[name].status() for name in self._order]
