"""Readiness, metrics, logs and request IDs for this service. #599

Logs, metrics and request IDs are `shared/`'s, bound here to this service's
name; traces join in a later step. This service has no database, so its one
dependency is Redis, and the Redis check lives here rather than in `shared/`:
it has one caller. ADR 0010, ADR 0012, ADR 0028.
"""

from __future__ import annotations

from collections.abc import Mapping

import redis.asyncio as redis
from fastapi import FastAPI
from redis.exceptions import RedisError

from core.config import Settings
from shared.http_metrics import HttpMetricsMiddleware, add_metrics_route
from shared.logs import configure_logging as _configure_logging
from shared.readiness import READY_TIMEOUT_SECONDS, Check, add_ready_route
from shared.request_ids import RequestContextMiddleware

__all__ = [
    "Check",
    "SERVICE_NAME",
    "configure_logging",
    "install",
    "is_redis_reachable",
    "open_ping_client",
]

SERVICE_NAME = "realtime"


def open_ping_client(settings: Settings) -> redis.Redis:
    """A client for `/ready` alone, whose timeouts match the route's. No I/O."""
    return redis.Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_timeout=READY_TIMEOUT_SECONDS,
        socket_connect_timeout=READY_TIMEOUT_SECONDS,
    )


async def is_redis_reachable(client: redis.Redis) -> bool:
    """`PING`. False when Redis cannot be reached."""
    try:
        await client.ping()
    except (RedisError, OSError):
        return False
    return True


def configure_logging() -> None:
    """JSON log lines on stdout, tagged with this service's name."""
    _configure_logging(service=SERVICE_NAME)


def install(app: FastAPI, *, ready_checks: Mapping[str, Check]) -> None:
    """Add the observability routes and middleware to `app`.

    Call it last in `create_app`, after CORS. Each added middleware wraps the
    ones before it, so the order outermost first is request context, then
    metrics, then CORS: metrics must wrap CORS or a preflight, which CORS
    answers by itself, would never be counted.
    """
    add_ready_route(app, ready_checks)
    add_metrics_route(app, ready_checks)
    app.add_middleware(HttpMetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
