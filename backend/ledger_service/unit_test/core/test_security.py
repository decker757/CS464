"""Token verification, and the absence of a minting path.

The verifier's own rules are tested once, in `shared/unit_test/test_security.py`.
"""

from __future__ import annotations

import pathlib
import uuid

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


def test_nothing_in_this_service_mints_a_token() -> None:
    """This service consumes identity and never issues it.

    Under HS256 the verifying secret could also sign, so nothing but this test
    holds the restriction (ADR 0002). `unit_test/` mints on purpose and is
    excluded.
    """
    # The shared package too: the verifier lives there, and a minting path
    # added there would reach every consuming service.
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
