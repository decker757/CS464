"""Token verification, and the absence of a minting path."""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from core.config import get_settings
from core.roles import UserRole
from core.security import decode_access_token
from unit_test.conftest import mint_token


def test_it_reads_a_well_formed_token() -> None:
    user_id = uuid.uuid4()

    claims = decode_access_token(mint_token(user_id, UserRole.ADMIN, username="mich"))

    assert claims is not None
    assert claims.user_id == user_id
    assert claims.username == "mich"
    assert claims.is_admin


def test_a_trader_is_not_an_admin() -> None:
    claims = decode_access_token(mint_token(uuid.uuid4(), UserRole.TRADER))

    assert claims is not None
    assert not claims.is_admin


def test_an_expired_token_is_refused() -> None:
    assert decode_access_token(mint_token(uuid.uuid4(), expires_in=-1)) is None


def test_a_token_signed_with_another_key_is_refused() -> None:
    assert decode_access_token(mint_token(uuid.uuid4(), secret="a" * 32)) is None


def test_a_token_from_another_issuer_is_refused() -> None:
    """Stops a token minted for a different system that happens to share our
    secret from being spent here."""
    assert decode_access_token(mint_token(uuid.uuid4(), issuer="somebody-else")) is None


def test_a_token_with_no_subject_is_refused() -> None:
    """`require` is what stops a stripped-down token arriving with no subject
    and being treated as somebody — which here means somebody's balance."""
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
    assert not claims.is_admin


def test_nothing_in_this_service_mints_a_token() -> None:
    """This service consumes identity and never issues it.

    Under HS256 the secret it verifies with would also let it sign, so the
    restriction is architectural rather than cryptographic and there is nothing
    but this test holding it. ADR 0002.

    `unit_test/` is excluded: the suite mints its own tokens on purpose, to
    exercise the same path a real request takes.
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
