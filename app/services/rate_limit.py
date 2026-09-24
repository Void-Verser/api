"""Per-API-key sliding-window rate limiter.

Redis-backed when configured, in-memory otherwise. Health endpoint is
never rate limited.
"""

from __future__ import annotations

import logging
import time
from collections import deque

log = logging.getLogger("media_api.ratelimit")


class RateLimiter:
    def __init__(self, redis_url: str = ""):
        self.redis_url = (redis_url or "").strip()
        self._redis = None
        self._mem: dict[str, deque] = {}

    async def _get_redis(self):
        if not self.redis_url:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            except Exception as exc:
                log.warning("redis init failed, memory rate limiter: %s", exc)
                return None
        return self._redis

    async def is_allowed(
        self, identity: str, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int, int]:
        """Return (allowed, remaining, reset_after_seconds)."""
        now = time.time()
        client = await self._get_redis()
        if client is not None:
            try:
                key = f"ratelimit:{identity}"
                pipe = client.pipeline()
                pipe.zremrangebyscore(key, 0, now - window_seconds)
                pipe.zcard(key)
                _, count = await pipe.execute()
                if count >= limit:
                    oldest = await client.zrange(key, 0, 0, withscores=True)
                    reset = int(window_seconds - (now - oldest[0][1])) if oldest else window_seconds
                    return False, 0, max(reset, 1)
                member = f"{now}:{time.monotonic_ns()}"
                pipe2 = client.pipeline()
                pipe2.zadd(key, {member: now})
                pipe2.expire(key, window_seconds + 5)
                await pipe2.execute()
                return True, max(limit - count - 1, 0), window_seconds
            except Exception as exc:
                log.warning("redis rate limit failed, memory fallback: %s", exc)

        dq = self._mem.setdefault(identity, deque())
        cutoff = now - window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            reset = int(window_seconds - (now - dq[0])) if dq else window_seconds
            return False, 0, max(reset, 1)
        dq.append(now)
        return True, max(limit - len(dq), 0), window_seconds

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None

    def reset(self) -> None:
        self._mem.clear()


_limiter: RateLimiter | None = None


def get_rate_limiter(settings=None) -> RateLimiter:
    global _limiter
    if _limiter is None:
        from app.config import get_settings

        s = settings or get_settings()
        _limiter = RateLimiter(redis_url=s.redis_url)
    return _limiter


def reset_rate_limiter_for_tests() -> None:
    global _limiter
    if _limiter is not None:
        _limiter.reset()
    _limiter = None
