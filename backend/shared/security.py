"""Access-token verification. [F-6] #76

The one copy of the rule that decides who a request is from. ADR 0005 put this
first on the list of things worth genuinely sharing, and it is the clearest
case in the repository: four services held byte-identical copies of this logic,
and every one of them is a place where a mistake grants somebody else's
authority.

**There is no minting path here, on purpose.** These services consume identity;
they never issue it. Under HS256 the secret used to verify would also sign, so
the restriction is architectural rather than cryptographic — ADR 0002 records
that, and the fix is RS256 with a published public key, at which point this
module only ever holds the public half.

This is also why the auth service does not import `decode_access_token` from
here for its own minting: `auth_service/core/security.py` keeps the signing
side, and ADR 0003 refused to share `core` precisely so that an encode path
could not leak into a service that must not have one. It verifies through this
module like everybody else.

**Settings arrive as arguments rather than being read.** Nothing in `shared/`
imports a service's `core.config`, because that would invert the dependency
that makes this package importable at all. Each service's `core/security.py`
binds its own settings and calls in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from shared.roles import UserRole


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    username: str
    role: UserRole
    expires_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    def seconds_until_expiry(self, *, now: datetime | None = None) -> float:
        """How long this token has left, floored at zero.

        Only the realtime service has to care. Every other service checks `exp`
        once per request and is done inside a few milliseconds; a socket
        accepted on a fifteen-minute token can still be open hours later, and
        an authorisation decision nobody ever revisits is not an authorisation
        decision. `realtime_service/controller/routes.py` closes the connection
        when this reaches zero. ADR 0010.
        """
        remaining = (self.expires_at - (now or datetime.now(UTC))).total_seconds()
        return max(remaining, 0.0)


def decode_access_token(
    token: str, *, secret: str, algorithm: str, issuer: str
) -> TokenClaims | None:
    """Return the claims, or None for any malformed, expired or foreign token.

    `require` is what stops an unsigned or stripped-down token from arriving
    with no subject and being treated as somebody. `issuer` is what stops a
    token minted for a different system that happens to share our secret.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            issuer=issuer,
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

    Now that `shared/roles.py` is the single vocabulary, the "newer deploy"
    case is the one that survives: services are no longer able to disagree
    about a role that has already shipped, only to meet one that has not
    reached them yet.
    """
    try:
        return UserRole(raw)
    except ValueError:
        return UserRole.TRADER
