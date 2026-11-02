"""Login and registration claim an attempt before they hash. [A-2] #600, ADR 0027.

Against the real Redis and Postgres. Hashing is spied on, never replaced: the
spies count calls and pass every argument through, so the outcome is the
service's own.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from uuid import uuid4

import pytest
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core import security
from core.cache import open_redis
from core.config import get_settings
from core.database import get_session_factory
from core.errors import InvalidCredentials, TooManyAttempts
from model.entities import User
from model.schemas import RegisterRequest
from service import auth_service
from service.attempt_limits import AttemptLimiter, AttemptLimits, Limit
from unit_test.conftest import TEST_IP, VALID_PASSWORD

WRONG_PASSWORD = "wrong-password-here"
OTHER_IP = "203.0.113.5"


def _counting(real: Callable, name: str, calls: Counter[str]) -> Callable:
    def count_then_call(*args: object, **kwargs: object) -> object:
        calls[name] += 1
        return real(*args, **kwargs)

    return count_then_call


def _counting_async(real: Callable, name: str, calls: Counter[str]) -> Callable:
    async def count_then_await(*args: object, **kwargs: object) -> object:
        calls[name] += 1
        return await real(*args, **kwargs)

    return count_then_await


@pytest.fixture
def hash_calls(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """How many times each hashing function ran, by name."""
    calls: Counter[str] = Counter()
    for name in ("verify_password", "dummy_verify", "hash_password"):
        monkeypatch.setattr(security, name, _counting(getattr(security, name), name, calls))
    return calls


async def _login(
    session: AsyncSession,
    limiter: AttemptLimiter,
    identifier: str,
    password: str,
    client_ip: str = TEST_IP,
) -> User:
    return await auth_service.authenticate(
        session, identifier, password, limiter=limiter, client_ip=client_ip
    )


async def _fail_login(
    session: AsyncSession,
    limiter: AttemptLimiter,
    identifier: str,
    client_ip: str = TEST_IP,
) -> None:
    with pytest.raises(InvalidCredentials):
        await _login(session, limiter, identifier, WRONG_PASSWORD, client_ip)


async def test_after_five_failures_even_the_right_password_is_refused_without_hashing(
    session: AsyncSession,
    registered_user: User,
    attempt_limiter: AttemptLimiter,
    hash_calls: Counter[str],
) -> None:
    for _ in range(5):
        await _fail_login(session, attempt_limiter, "ernest_t")
    verified_before = hash_calls["verify_password"]

    with pytest.raises(TooManyAttempts):
        await _login(session, attempt_limiter, "ernest_t", VALID_PASSWORD)

    assert verified_before == 5
    assert hash_calls["verify_password"] == verified_before


async def test_a_locked_username_leaves_its_email_unaffected(
    session: AsyncSession, registered_user: User, attempt_limiter: AttemptLimiter
) -> None:
    """Locking a public username must not show which email belongs to it."""
    for _ in range(5):
        await _fail_login(session, attempt_limiter, "ernest_t")
    with pytest.raises(TooManyAttempts):
        await _login(session, attempt_limiter, "ernest_t", VALID_PASSWORD)

    found = await _login(session, attempt_limiter, "ernest@example.com", VALID_PASSWORD)

    assert found.id == registered_user.id


async def test_an_unknown_account_is_refused_the_same_way(
    session: AsyncSession, attempt_limiter: AttemptLimiter, hash_calls: Counter[str]
) -> None:
    """After five failures an unknown name gets the same refusal as a real one,
    so the limit cannot be used to tell which accounts exist."""
    for _ in range(5):
        await _fail_login(session, attempt_limiter, "nobody_at_all")

    with pytest.raises(TooManyAttempts) as refused:
        await _fail_login(session, attempt_limiter, "nobody_at_all")

    assert 1 <= refused.value.retry_after_seconds <= 900
    assert hash_calls["dummy_verify"] == 5


async def test_spacing_and_case_variants_of_an_unknown_name_share_one_count(
    session: AsyncSession, attempt_limiter: AttemptLimiter
) -> None:
    for variant in ["ghost_user", "  GHOST_user ", "Ghost_User", "GHOST_USER", " ghost_user"]:
        await _fail_login(session, attempt_limiter, variant)

    with pytest.raises(TooManyAttempts):
        await _fail_login(session, attempt_limiter, "ghost_USER")


async def test_a_success_clears_the_account_counters(
    session: AsyncSession,
    registered_user: User,
    make_limiter: Callable[..., AttemptLimiter],
) -> None:
    """Both counters are capped at five here, so each one's clear is needed:
    without it the nine attempts below would overrun that counter."""
    limiter = make_limiter(login_account=Limit("login-account", 5, 3600))
    for _ in range(4):
        await _fail_login(session, limiter, "ernest_t")
    await _login(session, limiter, "ernest_t", VALID_PASSWORD)
    await session.commit()

    for _ in range(4):
        await _fail_login(session, limiter, "ernest_t")


async def test_a_lockout_from_one_address_does_not_reach_another(
    session: AsyncSession, registered_user: User, attempt_limiter: AttemptLimiter
) -> None:
    """A stranger's wrong guesses lock out the stranger's address, not the owner."""
    for _ in range(5):
        await _fail_login(session, attempt_limiter, "ernest_t", TEST_IP)
    with pytest.raises(TooManyAttempts):
        await _login(session, attempt_limiter, "ernest_t", VALID_PASSWORD, TEST_IP)

    found = await _login(session, attempt_limiter, "ernest_t", VALID_PASSWORD, OTHER_IP)

    assert found.id == registered_user.id


async def test_failures_spread_across_addresses_hit_the_account_cap(
    session: AsyncSession,
    registered_user: User,
    make_limiter: Callable[..., AttemptLimiter],
) -> None:
    limiter = make_limiter(login_account=Limit("login-account", 3, 3600))
    for address in ["198.51.100.1", "198.51.100.2", "198.51.100.3"]:
        await _fail_login(session, limiter, "ernest_t", address)

    with pytest.raises(TooManyAttempts):
        await _login(session, limiter, "ernest_t", VALID_PASSWORD, "198.51.100.4")


async def test_one_address_trying_many_usernames_is_refused(
    session: AsyncSession, make_limiter: Callable[..., AttemptLimiter]
) -> None:
    limiter = make_limiter(login_ip=Limit("login-ip", 3, 60))
    for name in ["first_guess", "second_guess", "third_guess"]:
        await _fail_login(session, limiter, name)

    with pytest.raises(TooManyAttempts):
        await _fail_login(session, limiter, "fourth_guess")


async def test_registration_is_refused_per_address_before_hashing(
    session: AsyncSession,
    make_limiter: Callable[..., AttemptLimiter],
    hash_calls: Counter[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = make_limiter(register_ip=Limit("register-ip", 2, 3600))
    lookups: Counter[str] = Counter()
    monkeypatch.setattr(
        auth_service,
        "_taken_fields",
        _counting_async(auth_service._taken_fields, "_taken_fields", lookups),
    )

    def request_for(name: str) -> RegisterRequest:
        return RegisterRequest(
            username=name, email=f"{name}@example.com", password=VALID_PASSWORD
        )

    for name in ["first_user", "second_user"]:
        await auth_service.register(
            session, request_for(name), limiter=limiter, client_ip=TEST_IP
        )
    hashed_before = hash_calls["hash_password"]
    looked_up_before = lookups["_taken_fields"]

    with pytest.raises(TooManyAttempts):
        await auth_service.register(
            session, request_for("third_user"), limiter=limiter, client_ip=TEST_IP
        )

    assert hashed_before == 2
    assert hash_calls["hash_password"] == hashed_before
    assert lookups["_taken_fields"] == looked_up_before


async def test_concurrent_wrong_logins_cannot_overshoot_the_limit(
    registered_user: User, hash_calls: Counter[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0015: eight wrong logins at once, all connected before they fire.

    Exactly five may reach the password check. A limiter that read the count
    and added to it afterwards would let more than five through.
    """
    racers = 8
    client = open_redis(get_settings())
    prefix = f"auth:rl:test:{uuid4().hex}"
    limiter = AttemptLimiter(client, AttemptLimits.from_settings(get_settings()), prefix)
    factory = get_session_factory()
    barrier = asyncio.Barrier(racers)

    # The user lookups finish at different times and would space the racers
    # out before the account claim, so a read-then-add limiter passed more
    # often than not. Meeting again after the lookup puts all eight at the
    # claim together; the wrapper returns the real lookup's answer.
    find_by_identifier = auth_service._find_by_identifier
    at_the_claim = asyncio.Barrier(racers)

    async def find_then_meet(session: AsyncSession, identifier: str) -> User | None:
        user = await find_by_identifier(session, identifier)
        await at_the_claim.wait()
        return user

    monkeypatch.setattr(auth_service, "_find_by_identifier", find_then_meet)

    async def wrong_login() -> None:
        async with factory() as own:
            await own.execute(text("SELECT 1"))
            await client.ping()
            await barrier.wait()
            await _login(own, limiter, "ernest_t", WRONG_PASSWORD)

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(wrong_login() for _ in range(racers)), return_exceptions=True),
            timeout=10,
        )
    finally:
        async for key in client.scan_iter(f"{prefix}:*"):
            await client.delete(key)
        await client.aclose()

    refused = [o for o in outcomes if isinstance(o, TooManyAttempts)]
    failed = [o for o in outcomes if isinstance(o, InvalidCredentials)]
    assert hash_calls["verify_password"] == 5
    assert (len(failed), len(refused)) == (5, 3)


async def test_login_still_works_when_redis_is_unreachable(
    session: AsyncSession, registered_user: User
) -> None:
    """Fail open: a Redis outage must not lock everybody out. ADR 0027."""
    client = redis.from_url(
        "redis://127.0.0.1:1/0", socket_timeout=0.5, socket_connect_timeout=0.5
    )
    limiter = AttemptLimiter(
        client, AttemptLimits.from_settings(get_settings()), "auth:rl:test:down"
    )
    try:
        for _ in range(10):
            await _fail_login(session, limiter, "ernest_t")
        found = await _login(session, limiter, "ernest_t", VALID_PASSWORD)
    finally:
        await client.aclose()

    assert found.id == registered_user.id
