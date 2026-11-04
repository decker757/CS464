"""The limiter's decisions and fail-opens are counted. #599.

The registry is process-wide, so every assertion is a difference between a
reading before the call and one after it.
"""

from __future__ import annotations

import pytest
import redis.asyncio as redis
from prometheus_client import REGISTRY, generate_latest

from core.config import get_settings
from core.errors import TooManyAttempts
from core.metrics import LIMIT_NAMES
from service.attempt_limits import AttemptLimiter, AttemptLimits, Limit

DECISIONS = "auth_limiter_decisions_total"
FAIL_OPEN = "auth_limiter_fail_open_total"


def _reading(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _decisions(limit: str) -> tuple[float, float]:
    return (
        _reading(DECISIONS, limit=limit, outcome="allowed"),
        _reading(DECISIONS, limit=limit, outcome="refused"),
    )


@pytest.fixture
async def unreachable_limiter():
    """A limiter whose Redis refuses every connection, so every call fails open."""
    client = redis.from_url(
        "redis://127.0.0.1:1/0", socket_timeout=0.5, socket_connect_timeout=0.5
    )
    limits = AttemptLimits.from_settings(get_settings())
    yield AttemptLimiter(client, limits, "auth:rl:test:unreachable")
    await client.aclose()


async def test_a_refused_claim_is_counted_against_its_limit(make_limiter):
    limit = Limit("login-ip", 1, 60)
    limiter = make_limiter(login_ip=limit)
    allowed_before, refused_before = _decisions("login-ip")

    await limiter.claim(limit, "a")
    with pytest.raises(TooManyAttempts):
        await limiter.claim(limit, "a")

    allowed_after, refused_after = _decisions("login-ip")
    assert allowed_after - allowed_before == 1
    assert refused_after - refused_before == 1


async def test_a_redis_outage_is_counted_as_the_limiter_being_off(unreachable_limiter):
    limit = unreachable_limiter.limits.login_ip
    fail_open_before = _reading(FAIL_OPEN, limit="login-ip")
    decisions_before = _decisions("login-ip")

    await unreachable_limiter.claim(limit, "a")

    assert _reading(FAIL_OPEN, limit="login-ip") - fail_open_before == 1
    assert _decisions("login-ip") == decisions_before


async def test_a_redis_outage_while_clearing_is_counted_too(unreachable_limiter):
    limit = unreachable_limiter.limits.login_ip
    fail_open_before = _reading(FAIL_OPEN, limit="login-ip")
    decisions_before = _decisions("login-ip")

    await unreachable_limiter.clear(limit, "a")

    assert _reading(FAIL_OPEN, limit="login-ip") - fail_open_before == 1
    assert _decisions("login-ip") == decisions_before


def test_every_limit_the_limiter_has_is_pre_registered():
    limits = AttemptLimits.from_settings(get_settings())
    assert set(LIMIT_NAMES) == {
        limits.login_account_ip.name,
        limits.login_account.name,
        limits.login_ip.name,
        limits.register_ip.name,
    }


async def test_no_metric_carries_an_address_or_an_account(make_limiter):
    limit = Limit("login-ip", 5, 60)
    limiter = make_limiter(login_ip=limit)

    await limiter.claim(limit, "198.51.100.7")
    await limiter.claim(limit, "7c1d2b0e-0000-4000-8000-000000000000:198.51.100.7")

    assert "198.51.100.7" not in generate_latest().decode()
