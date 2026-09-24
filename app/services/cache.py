"""Cache service: Redis when configured, in-memory fallback otherwise.

Only small, safe payloads (search results, metadata) are cached with TTL.
Large media files are never cached. Failures degrade to pass-through.
"""

from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("media_api.cache")


class CacheService:
    def __init__(self, redis_url: str = "", default_ttl: int = 300):
        self.redis_url = (redis_url or "").strip()
        self.default_ttl = default_ttl
        self._redis = None
        self._mem: dict[str, tuple[float, str]] = {}

    async def _get_redis(self):
        if not self.redis_url:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            except Exception as exc:
                log.warning("redis init failed, using memory cache: %s", exc)
                return None
        return self._redis

    async def get(self, key: str) -> str | None:
        # Try redis first
        client = await self._get_redis()
        if client is not None:
            try:
                return await client.get(key)
            except Exception as exc:
                log.warning("redis get failed, memory fallback: %s", exc)
        # Memory fallback
        item = self._mem.get(key)
        if not item:
            return None
        exp, value = item
        if exp < time.time():
            self._mem.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        ttl = ttl or self.default_ttl
        client = await self._get_redis()
        if client is not None:
            try:
                await client.setex(key, ttl, value)
            except Exception as exc:
                log.warning("redis set failed, memory fallback: %s", exc)
        self._mem[key] = (time.time() + ttl, value)

    async def delete(self, key: str) -> None:
        client = await self._get_redis()
        if client is not None:
            try:
                await client.delete(key)
            except Exception:
                pass
        self._mem.pop(key, None)

    async def get_json(self, key: str):
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_json(self, key: str, obj, ttl: int | None = None) -> None:
        await self.set(key, json.dumps(obj), ttl)

    async def ping(self) -> bool:
        client = await self._get_redis()
        if client is None:
            return True  # memory cache always "up"
        try:
            await client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None

    def reset_memory(self) -> None:
        self._mem.clear()


_cache: CacheService | None = None


def get_cache(settings=None) -> CacheService:
    global _cache
    if _cache is None:
        from app.config import get_settings

        s = settings or get_settings()
        _cache = CacheService(redis_url=s.redis_url)
    return _cache


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
