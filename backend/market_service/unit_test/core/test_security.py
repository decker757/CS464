"""Token verification. No database, no HTTP.

This is the whole of the service's trust model, so it is asserted directly
rather than only through a route.

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
    """Architectural guard, not a cryptographic one: under HS256 this service
    could sign, and must have no code that does. ADR 0002."""
    exported = dir(security)

    assert not [name for name in exported if "create" in name or "encode" in name]

    # The verifier lives in `shared/`, where `dir()` above cannot see a new
    # encoder, so scan both trees for the call itself.
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
