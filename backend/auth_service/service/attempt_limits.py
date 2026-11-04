"""Fixed-window attempt counters in Redis. [A-2] #600, ADR 0027.

`claim` takes a slot *before* the work it guards runs, never "read, work, then
add": with that order 200 simultaneous logins all read zero and all run Argon2.
A success calls `clear` to hand the slots back; a failure just leaves its claim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import redis.asyncio as redis
from redis.exceptions import RedisError

from core.config import Settings
from core.errors import TooManyAttempts
from core.metrics import LIMITER_DECISIONS, LIMITER_FAIL_OPEN

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    """One counter: how many attempts fit in how many seconds."""

    name: str
    max_attempts: int
    window_seconds: int


@dataclass(frozen=True)
class AttemptLimits:
    """The four limits the auth service enforces."""

    login_account_ip: Limit
    login_account: Limit
    login_ip: Limit
    register_ip: Limit

    @classmethod
    def from_settings(cls, settings: Settings) -> AttemptLimits:
        """Build the four limits from the configured settings."""
        return cls(
            login_account_ip=Limit(
                "login-account-ip",
                settings.login_failures_per_account_ip,
                settings.login_failures_per_account_ip_window_seconds,
            ),
            login_account=Limit(
                "login-account",
                settings.login_failures_per_account,
                settings.login_failures_per_account_window_seconds,
            ),
            login_ip=Limit(
                "login-ip",
                settings.login_attempts_per_ip,
                settings.login_attempts_per_ip_window_seconds,
            ),
            register_ip=Limit(
                "register-ip",
                settings.registrations_per_ip,
                settings.registrations_per_ip_window_seconds,
            ),
        )


class AttemptLimiter:
    """Counts attempts in Redis and fails open when Redis is unreachable."""

    def __init__(self, client: redis.Redis, limits: AttemptLimits, key_prefix: str) -> None:
        self._client = client
        self.limits = limits
        self._key_prefix = key_prefix

    def _key(self, limit: Limit, subject: str) -> str:
        return f"{self._key_prefix}:{limit.name}:{subject}"

    async def claim(self, limit: Limit, subject: str) -> None:
        """Take one slot, or raise `TooManyAttempts` if the window is full.

        One atomic round trip. `EXPIRE ... NX` sets the window only on the
        first claim, so refused claims never extend it.
        """
        key = self._key(limit, subject)
        try:
            pipe = self._client.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, limit.window_seconds, nx=True)
            pipe.ttl(key)
            count, _, ttl = await pipe.execute()
        # The fifth sanctioned catch (ADR 0027): a Redis outage must not lock
        # everyone out, so the limiter answers "allowed". Only Redis's own
        # error types, which include its timeouts - never bare Exception.
        except RedisError:
            LIMITER_FAIL_OPEN.labels(limit=limit.name).inc()
            logger.warning("attempt limiter unavailable; allowing the request (limit=%s)", limit.name)
            return
        if count > limit.max_attempts:
            LIMITER_DECISIONS.labels(limit=limit.name, outcome="refused").inc()
            raise TooManyAttempts(max(ttl, 1))
        LIMITER_DECISIONS.labels(limit=limit.name, outcome="allowed").inc()

    async def clear(self, limit: Limit, subject: str) -> None:
        """Reset the count; fails open like `claim`."""
        try:
            await self._client.delete(self._key(limit, subject))
        # Sanctioned fail-open catch, as in `claim` (ADR 0027).
        except RedisError:
            LIMITER_FAIL_OPEN.labels(limit=limit.name).inc()
            logger.warning("attempt limiter unavailable; the count was not cleared (limit=%s)", limit.name)
