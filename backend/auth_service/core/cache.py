"""The Redis client the attempt limiter counts in. [A-2] #600, ADR 0027."""

import redis.asyncio as redis

from core.config import Settings


def open_redis(settings: Settings) -> redis.Redis:
    """Build the client. Lazy: nothing connects until the first command.

    Both timeouts are set because without them a Redis that never answers makes
    a login wait instead of fail, and the limiter fails open only on an error.
    """
    timeout = settings.rate_limit_redis_timeout_seconds
    return redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
    )
