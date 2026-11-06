"""Fixed-window rate limiter backed by Redis.

Fails *open* when Redis is unavailable (logged): an outage of the limiter must not take the API down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_in_seconds: int


class RateLimiter:
    def __init__(self, redis: Redis | None, *, enabled: bool = True, prefix: str = "rl") -> None:
        self._redis = redis
        self._enabled = enabled and redis is not None
        self._prefix = prefix

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
        if not self._enabled or limit <= 0:
            return RateLimitResult(True, limit, limit, 0)
        assert self._redis is not None

        now = time.time()
        bucket = int(now // window_seconds)
        reset_in = int(window_seconds - (now % window_seconds)) + 1
        redis_key = f"{self._prefix}:{key}:{bucket}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds + 1)
                count, _ = await pipe.execute()
        except RedisError:
            log.warning("rate_limiter_unavailable", scope=key.split(":", 1)[0])
            return RateLimitResult(True, limit, limit, 0)

        count = int(count)
        return RateLimitResult(count <= limit, limit, max(limit - count, 0), reset_in)
