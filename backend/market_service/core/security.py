"""Access-token verification, bound to this service's settings. [F-6] #76

The rule itself is in `shared/security.py`, which four services held identical
copies of before #76. What stays here is the binding: the shared verifier takes
its secret, algorithm and issuer as arguments so that nothing in `shared/`
imports a service's `core.config`, and this is where this service supplies its
own.

There is still no minting path reachable from this service, which is the
property `unit_test/core/test_security.py` asserts. Under HS256 the secret used
to verify would also sign, so the restriction is architectural rather than
cryptographic — ADR 0002 records that, and the fix is RS256 with a published
public key.
"""

from __future__ import annotations

from core.config import get_settings
from shared import security as _shared
from shared.security import TokenClaims

__all__ = ["TokenClaims", "decode_access_token"]


def decode_access_token(token: str) -> TokenClaims | None:
    """Return the claims, or None for any malformed, expired or foreign token."""
    settings = get_settings()
    return _shared.decode_access_token(
        token,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
    )
