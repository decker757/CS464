"""Token verification. No database, no HTTP.

This is the whole of the service's trust model, so it is asserted directly
rather than only through a route.
"""

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
    """Architectural guard, not a cryptographic one.

    Under HS256 this service holds a secret that would let it sign. The
    restriction is that it has no code to do so, and this test is what keeps a
    convenience helper from quietly appearing. ADR 0002 records the real fix.
    """
    exported = dir(security)

    assert not [name for name in exported if "create" in name or "encode" in name]

    # `core.security` is a five-line wrapper since [F-6] #76, so the check above
    # now inspects almost nothing: the verifier itself lives in
    # `backend/shared/security.py`, and a `create_access_token` added there
    # would reach this service without changing anything `dir()` can see. Scan
    # both trees for the encode call itself.
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
    """The forgery case. A signature check that passed here would be the whole
    authorisation model gone."""
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
    """alg=none is the classic JWT bypass. PyJWT refuses it because
    `algorithms` names HS256 explicitly, which is worth pinning down."""
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
    """A role only a newer auth deploy knows about must not grant authority
    here, and must not reject an otherwise valid token either."""
    claims = security.decode_access_token(_hand_rolled(role=role))

    assert claims is not None
    assert claims.role is UserRole.TRADER


def test_a_token_with_no_subject_is_rejected() -> None:
    """Not merely anonymous: a claims object with no user_id would make every
    creator-scoped query match nothing, or worse, everything."""
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
