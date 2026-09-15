"""Token verification, and the absence of a minting path."""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from core.config import get_settings
from core.roles import UserRole
from core.security import TokenClaims, decode_access_token
from unit_test.conftest import mint_token


def test_a_valid_token_decodes() -> None:
    user_id = uuid.uuid4()

    claims = decode_access_token(mint_token(user_id, username="michelle_t"))

    assert claims is not None
    assert claims.user_id == user_id
    assert claims.username == "michelle_t"
    assert claims.role is UserRole.TRADER


def test_a_token_signed_with_another_key_is_refused() -> None:
    token = mint_token(secret="a-different-secret-entirely-and-long-enough")

    assert decode_access_token(token) is None


def test_a_token_from_another_issuer_is_refused() -> None:
    """Every service here shares one HS256 secret, so the signature alone does
    not say the token was minted for this system. ADR 0002."""
    token = mint_token(issuer="somebody-elses-auth")

    assert decode_access_token(token) is None


def test_an_expired_token_is_refused() -> None:
    assert decode_access_token(mint_token(expires_in=-1)) is None


def test_garbage_is_refused() -> None:
    assert decode_access_token("not-a-token") is None


def test_a_token_with_no_subject_is_refused() -> None:
    """`require` is what stops a stripped-down token arriving with no subject
    and being treated as somebody."""
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {"iss": settings.jwt_issuer, "iat": now, "exp": now + timedelta(minutes=5)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(token) is None


def test_an_unrecognised_role_fails_closed() -> None:
    """A role only a newer auth deploy knows about is not grounds for rejecting
    an otherwise valid token, and not grounds for granting authority either."""
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "username": "ernest_t",
            "role": "superuser",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    claims = decode_access_token(token)

    assert claims is not None
    assert claims.role is UserRole.TRADER


# ---------------------------------------------------------------------------
# seconds_until_expiry — the one thing this service's copy adds
# ---------------------------------------------------------------------------


def test_seconds_until_expiry_counts_down() -> None:
    """The number the socket's expiry watchdog sleeps on."""
    claims = decode_access_token(mint_token(expires_in=900))

    assert claims is not None
    assert 890 < claims.seconds_until_expiry() <= 900


def test_seconds_until_expiry_floors_at_zero() -> None:
    """Never negative, because it is passed to `asyncio.sleep`.

    A negative sleep raises, and it would raise inside the task supervising a
    connection that had just been authorised — turning "this token is already
    expired" into an internal error. `decode_access_token` refuses an expired
    token, so this is the defence for a token that expires between the decode
    and the watchdog starting.
    """
    already_gone = TokenClaims(
        user_id=uuid.uuid4(),
        username="ernest_t",
        role=UserRole.TRADER,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    assert already_gone.seconds_until_expiry() == 0.0


def test_seconds_until_expiry_takes_an_injected_clock() -> None:
    expires_at = datetime.now(UTC) + timedelta(seconds=300)
    claims = TokenClaims(
        user_id=uuid.uuid4(),
        username="ernest_t",
        role=UserRole.TRADER,
        expires_at=expires_at,
    )

    remaining = claims.seconds_until_expiry(now=expires_at - timedelta(seconds=42))

    assert remaining == pytest.approx(42.0)


def test_nothing_in_this_service_mints_a_token() -> None:
    """This service consumes identity and never issues it.

    Under HS256 the secret it verifies with would also let it sign, so the
    restriction is architectural rather than cryptographic and there is nothing
    but this test holding it. ADR 0002.
    """
    service_root = pathlib.Path(__file__).resolve().parents[2]

    encoders = [
        path
        for path in service_root.rglob("*.py")
        if ".venv" not in path.parts
        and "unit_test" not in path.parts
        and "jwt.encode" in path.read_text()
    ]

    assert encoders == []
