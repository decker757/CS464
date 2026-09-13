"""Password hashing and token handling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from core import security
from core.config import get_settings


def test_hash_is_not_the_plaintext_and_is_salted() -> None:
    password = "correct-horse-battery-staple"
    first = security.hash_password(password)
    second = security.hash_password(password)

    assert password not in first
    assert first.startswith("$argon2id$")
    # Distinct salts, so two users with the same password get different hashes.
    assert first != second


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [("correct-horse-battery-staple", True), ("wrong-password", False), ("", False)],
)
def test_verify_password(attempt: str, expected: bool) -> None:
    stored = security.hash_password("correct-horse-battery-staple")
    assert security.verify_password(stored, attempt) is expected


def test_verify_password_rejects_a_corrupt_hash() -> None:
    assert security.verify_password("not-a-hash", "anything") is False


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token = security.create_access_token(user_id, "ernest_t")

    claims = security.decode_access_token(token)

    assert claims is not None
    assert claims.user_id == user_id
    assert claims.username == "ernest_t"
    assert claims.expires_at > datetime.now(UTC)


def test_expired_token_is_rejected() -> None:
    settings = get_settings()
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "username": "ernest_t",
            "iss": settings.jwt_issuer,
            "iat": past,
            "exp": past + timedelta(seconds=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert security.decode_access_token(expired) is None


def test_token_signed_with_another_key_is_rejected() -> None:
    settings = get_settings()
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "username": "attacker",
            "iss": settings.jwt_issuer,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        "a-different-secret-of-sufficient-length",
        algorithm="HS256",
    )

    assert security.decode_access_token(forged) is None


def test_garbage_token_is_rejected() -> None:
    assert security.decode_access_token("not.a.token") is None


def test_refresh_token_is_stored_only_as_a_hash() -> None:
    raw, stored = security.generate_refresh_token()

    assert raw != stored
    assert len(stored) == 64  # sha256 hex
    assert security.hash_refresh_token(raw) == stored
    assert security.hash_refresh_token("some-other-token") != stored
