"""Token verification, the whole of this service's trust model. No database, no HTTP."""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from core import security
from core.config import get_settings
from core.roles import UserRole
from unit_test.conftest import mint_token


def test_there_is_no_way_to_mint_a_token_from_this_service() -> None:
    """Architectural guard: under HS256 the secret could sign, so the rule is
    that no code does. ADR 0002."""
    exported = dir(security)

    assert not [name for name in exported if "create" in name or "encode" in name]

    # `core.security` only wraps `shared/security.py`, where a new encoder would
    # be invisible to `dir()`. Scan both trees for the encode call itself.
    service_root = pathlib.Path(__file__).resolve().parents[2]
    shared_root = service_root.parent / "shared"

    encoders = [
        path
        for root in (service_root, shared_root)
        for path in root.rglob("*.py")
        if ".venv" not in path.parts
        and "unit_test" not in path.parts
        and "jwt.encode" in path.read_text()
    ]

    assert encoders == []


@pytest.mark.parametrize("role", list(UserRole))
def test_a_valid_token_yields_its_claims(role: UserRole) -> None:
    user_id = uuid.uuid4()

    claims = security.decode_access_token(mint_token(user_id, role, username="ihsan"))

    assert claims is not None
    assert claims.user_id == user_id
    assert claims.username == "ihsan"
    assert claims.role is role
    assert claims.is_admin is (role is UserRole.ADMIN)


def test_an_expired_token_is_rejected() -> None:
    token = mint_token(uuid.uuid4(), expires_in=-1)

    assert security.decode_access_token(token) is None


def test_a_token_signed_with_another_key_is_rejected() -> None:
    """The forgery case."""
    token = mint_token(uuid.uuid4(), secret="b" * 40)

    assert security.decode_access_token(token) is None


def test_a_token_from_another_issuer_is_rejected() -> None:
    """Covers a token minted for a different system that shares our secret."""
    token = mint_token(uuid.uuid4(), issuer="somebody-else")

    assert security.decode_access_token(token) is None


@pytest.mark.parametrize("token", ["", "not.a.token", "Bearer x"])
def test_malformed_tokens_are_rejected(token: str) -> None:
    assert security.decode_access_token(token) is None


def test_an_unsigned_token_is_rejected() -> None:
    """alg=none, the classic bypass, refused because `algorithms` is explicit."""
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "username": "attacker",
            "role": "admin",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        key="",
        algorithm="none",
    )

    assert security.decode_access_token(token) is None


def _hand_rolled(**claims: object) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "username": "ernest_t",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_a_token_with_no_role_claim_is_read_as_a_trader() -> None:
    """Fail closed. Covers a token minted before auth grew the claim."""
    claims = security.decode_access_token(_hand_rolled())

    assert claims is not None
    assert claims.role is UserRole.TRADER
    assert claims.is_admin is False


@pytest.mark.parametrize("role", ["super_admin", "ADMIN", "", None, 7, ["admin"]])
def test_an_unrecognised_role_is_read_as_a_trader(role: object) -> None:
    """A role from a newer auth deploy grants nothing, and rejects nothing."""
    claims = security.decode_access_token(_hand_rolled(role=role))

    assert claims is not None
    assert claims.role is UserRole.TRADER


def test_a_token_with_no_subject_is_rejected() -> None:
    """Claims with no user_id would make an actor-scoped query match anything."""
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "username": "ernest_t",
            "role": "admin",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    assert security.decode_access_token(token) is None
