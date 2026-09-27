"""Token verification, the whole of this service's trust model. No database, no HTTP.

The verifier's own rules are tested once, in `shared/unit_test/test_security.py`.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest

from core import security
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


def test_a_token_from_another_issuer_is_rejected() -> None:
    """Covers a token minted for a different system that shares our secret.

    Not a copy of the shared test: it proves this service's seam passes the
    issuer at all, which the valid-token test above cannot.
    """
    token = mint_token(uuid.uuid4(), issuer="somebody-else")

    assert security.decode_access_token(token) is None
