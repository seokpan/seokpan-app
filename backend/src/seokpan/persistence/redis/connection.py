"""Validated Redis client lifecycle for the production Application runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from redis.asyncio import Redis

from seokpan.settings import Settings

REDIS_HOST = "redis.platform.svc.cluster.local"
REDIS_PORT = 6379
REDIS_DATABASE = 0


class RedisConfigurationError(ValueError):
    """Safe configuration failure that never includes the supplied URL."""


def validated_redis_url(raw_url: str | None) -> str:
    if not raw_url or any(ord(char) < 32 or ord(char) == 127 for char in raw_url):
        raise RedisConfigurationError("Redis URL is missing or malformed")
    try:
        parts = urlsplit(raw_url)
        port = REDIS_PORT if parts.port is None else parts.port
    except ValueError:
        raise RedisConfigurationError("Redis URL is malformed") from None
    if (
        parts.scheme != "redis"
        or parts.hostname != REDIS_HOST
        or port != REDIS_PORT
        or parts.path != f"/{REDIS_DATABASE}"
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise RedisConfigurationError("Redis URL must use the official service endpoint")
    return f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DATABASE}"


@asynccontextmanager
async def runtime_redis(settings: Settings) -> AsyncIterator[Redis]:
    """Create one lazy client per Application lifetime and always close its pool."""

    url = validated_redis_url(settings.redis_url)
    client = Redis.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
        retry_on_timeout=False,
    )
    try:
        yield client
    finally:
        await client.aclose()
