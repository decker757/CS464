"""The attempt limiter against a real Redis, no mocks. [A-2] #600, ADR 0027."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import redis.asyncio as redis

from core.cache import open_redis
from core.config import get_settings
from core.errors import TooManyAttempts
from service.attempt_limits import AttemptLimiter, AttemptLimits, Limit


async def test_a_claim_over_the_limit_is_refused_with_the_windows_remaining_time(make_limiter):
    limit = Limit("login-ip", 3, 60)
    limiter = make_limiter(login_ip=limit)

    for _ in range(3):
        await limiter.claim(limit, "a")
    with pytest.raises(TooManyAttempts) as refused:
        await limiter.claim(limit, "a")

    assert 1 <= refused.value.retry_after_seconds <= 60


async def test_a_refused_claim_does_not_extend_the_window():
    limit = Limit("login-ip", 1, 2)
    prefix = f"auth:rl:test:{uuid4().hex}"
    client = open_redis(get_settings())
    limiter = AttemptLimiter(client, AttemptLimits.from_settings(get_settings()), prefix)
    key = f"{prefix}:{limit.name}:a"
    try:
        await limiter.claim(limit, "a")
        # Refusals that land in the same instant as the first claim would hide
        # a window that was reset, so let part of the window pass first.
        await asyncio.sleep(0.5)
        for _ in range(3):
            with pytest.raises(TooManyAttempts):
                await limiter.claim(limit, "a")

        remaining_ms = await client.pttl(key)
        assert 0 < remaining_ms <= limit.window_seconds * 1000 - 400
    finally:
        await client.delete(key)
        await client.aclose()


async def test_clearing_resets_the_count(make_limiter):
    limit = Limit("login-account", 2, 60)
    limiter = make_limiter(login_account=limit)

    await limiter.claim(limit, "a")
    await limiter.claim(limit, "a")
    await limiter.clear(limit, "a")
    await limiter.claim(limit, "a")
    await limiter.claim(limit, "a")

    with pytest.raises(TooManyAttempts):
        await limiter.claim(limit, "a")


async def test_two_limiters_over_one_redis_share_one_count():
    limit = Limit("login-ip", 2, 60)
    limits = AttemptLimits.from_settings(get_settings())
    prefix = f"auth:rl:test:{uuid4().hex}"
    first_client = open_redis(get_settings())
    second_client = open_redis(get_settings())
    first = AttemptLimiter(first_client, limits, prefix)
    second = AttemptLimiter(second_client, limits, prefix)
    try:
        await first.claim(limit, "a")
        await second.claim(limit, "a")
        with pytest.raises(TooManyAttempts):
            await first.claim(limit, "a")
    finally:
        await first.clear(limit, "a")
        await first_client.aclose()
        await second_client.aclose()


async def test_an_unreachable_redis_lets_every_claim_through():
    limit = Limit("login-ip", 1, 60)
    client = redis.from_url(
        "redis://127.0.0.1:1/0", socket_timeout=0.5, socket_connect_timeout=0.5
    )
    limiter = AttemptLimiter(
        client, AttemptLimits.from_settings(get_settings()), "auth:rl:test:down"
    )
    try:
        for _ in range(10):
            await limiter.claim(limit, "a")
        await limiter.clear(limit, "a")
    finally:
        await client.aclose()
