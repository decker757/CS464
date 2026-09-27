"""Access-token verification, bound to this service's settings. [F-6] #76

The rule itself is in `shared/security.py`; this is the seam (ADR 0012). This
service never mints a token, and `unit_test/core/test_security.py` fails if a
minting path appears. ADR 0002.
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
