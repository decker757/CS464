"""Password hashing and token handling. No database, no HTTP.

The verifier's own rules are tested once, in `shared/unit_test/test_security.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from core import security
from core.config import get_settings
from core.roles import UserRole


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
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert security.decode_access_token(token) is None


def test_the_role_travels_as_a_plain_string_claim() -> None:
    """Asserted on the wire, since other services read this claim. ADR 0003."""
    token = security.create_access_token(uuid.uuid4(), "ernest_t", UserRole.ADMIN)

    payload = jwt.decode(
        token,
        get_settings().jwt_secret,
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
