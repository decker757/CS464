"""Access-token verification.

There is no minting path in this file, on purpose. This service consumes
identity; it never issues it. Under HS256 the secret it verifies with would
also let it sign, so the restriction is architectural rather than
cryptographic — ADR 0002 records that, and the fix is RS256 with a published
public key, at which point this file only ever holds the public half.

Nothing else in the service imports jwt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from core.config import get_settings
from core.roles import UserRole


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    username: str
    role: UserRole
    expires_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN


def decode_access_token(token: str) -> TokenClaims | None:
    """Return the claims, or None for any malformed, expired or foreign token.

    `require` is what stops an unsigned or stripped-down token from arriving
    with no subject and being treated as somebody. `issuer` is what stops a
    token minted for a different system that happens to share our secret.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            username=payload["username"],
            role=_read_role(payload.get("role")),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def _read_role(raw: object) -> UserRole:
    """Fail closed: anything unrecognised is the least privileged role.

    Covers a token minted before the claim existed and a token carrying a role
    only a newer auth deploy knows about. Neither is grounds for rejecting an
    otherwise valid token, and neither is grounds for granting authority.
    """
    try:
        return UserRole(raw)
    except ValueError:
        return UserRole.TRADER
