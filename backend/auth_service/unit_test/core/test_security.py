"""Password hashing and token handling. No database, no HTTP.

The verifier's own rules are tested once, in `shared/unit_test/test_security.py`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from core import security
from core.config import get_settings
from core.roles import UserRole


async def test_hash_is_not_the_plaintext_and_is_salted() -> None:
    password = "correct-horse-battery-staple"
    first = await security.hash_password(password)
    second = await security.hash_password(password)

    assert password not in first
    assert first.startswith("$argon2id$")
    # Distinct salts, so two users with the same password get different hashes.
    assert first != second


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [("correct-horse-battery-staple", True), ("wrong-password", False), ("", False)],
)
async def test_verify_password(attempt: str, expected: bool) -> None:
    stored = await security.hash_password("correct-horse-battery-staple")
    assert await security.verify_password(stored, attempt) is expected


async def test_verify_password_rejects_a_corrupt_hash() -> None:
    assert await security.verify_password("not-a-hash", "anything") is False


async def _longest_stall_while(work: Callable[[], Awaitable[object]]) -> tuple[float, float]:
    """Run `work` a few times beside a heartbeat; return (longest gap, time taken).

    The heartbeat asks for a 5 ms sleep, so a gap far longer than that means
    something held the event loop.
    """
    gaps: list[float] = []
    finished = asyncio.Event()

    async def heartbeat() -> None:
        last_beat = time.perf_counter()
        while not finished.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last_beat)
            last_beat = now

    beating = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)  # let the heartbeat take its first sleep
    started = time.perf_counter()
    for _ in range(4):
        await work()
    taken = time.perf_counter() - started
    finished.set()
    await beating
    return max(gaps), taken


@pytest.mark.parametrize("call", ["hash_password", "verify_password", "dummy_verify"])
async def test_argon2_leaves_the_event_loop_free(call: str) -> None:
    """While a hash or verify runs, another coroutine on the loop still runs. #637

    On the loop, Argon2 stalls the limiter's Redis calls past their timeout,
    which fails the login limits open (ADR 0027). Bounded against the work's
    own duration rather than a fixed time, so a slow machine cannot flip it:
    held on the loop, the longest gap is all four calls back to back.
    """
    stored = await security.hash_password("correct-horse-battery-staple")
    calls: dict[str, Callable[[], Awaitable[object]]] = {
        "hash_password": lambda: security.hash_password("correct-horse-battery-staple"),
        "verify_password": lambda: security.verify_password(stored, "wrong-password"),
        "dummy_verify": security.dummy_verify,
    }

    longest_gap, taken = await _longest_stall_while(calls[call])

    assert longest_gap < taken / 2


@pytest.mark.parametrize("role", list(UserRole))
def test_access_token_round_trip(role: UserRole) -> None:
    user_id = uuid.uuid4()
    token = security.create_access_token(user_id, "ernest_t", role)

    claims = security.decode_access_token(token)

    assert claims is not None
    assert claims.user_id == user_id
    assert claims.username == "ernest_t"
    assert claims.role is role
    assert claims.expires_at > datetime.now(UTC)


def test_a_token_from_another_issuer_is_rejected() -> None:
    """Covers a token minted for a different system that shares our secret.

    Not a copy of the shared test: it proves this service's seam passes the
    issuer at all, which the round trip above cannot.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "username": "ernest_t",
            "iss": "somebody-else",
            "iat": now,
            "exp": now + timedelta(minutes=15),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    assert security.decode_access_token(token) is None


def test_the_role_travels_as_a_plain_string_claim() -> None:
    """Asserted on the wire, since other services read this claim. ADR 0003."""
    token = security.create_access_token(uuid.uuid4(), "ernest_t", UserRole.ADMIN)

    payload = jwt.decode(
        token,
        get_settings().jwt_secret.get_secret_value(),
        algorithms=[get_settings().jwt_algorithm],
        issuer=get_settings().jwt_issuer,
    )

    assert payload["role"] == "admin"


def test_refresh_token_is_stored_only_as_a_hash() -> None:
    raw, stored = security.generate_refresh_token()

    assert raw != stored
    assert len(stored) == 64  # sha256 hex
    assert security.hash_refresh_token(raw) == stored
    assert security.hash_refresh_token("some-other-token") != stored
