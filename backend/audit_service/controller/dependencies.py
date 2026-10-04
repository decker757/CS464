"""FastAPI dependencies: the request's session, its claims, and the admin guard.

Dependencies rather than middleware, so /docs states which routes need a token
and a route receives typed claims. No lookup in auth.users: there is no grant,
and every name shown was snapshotted into the row.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from controller import transport
from core import security
from core.database import get_session
from core.errors import NotAnAdministrator, NotAuthenticated
from core.security import TokenClaims

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_claims(request: Request) -> TokenClaims:
    """Return the verified claims of the caller's access token.

    Raises NotAuthenticated. No database access: the token is self-contained.
    """
    token = transport.extract_access_token(request)
    if token is None:
        raise NotAuthenticated

    claims = security.decode_access_token(token)
    if claims is None:
        raise NotAuthenticated

    return claims


CurrentUser = Annotated[TokenClaims, Depends(get_claims)]


async def require_admin(claims: CurrentUser) -> TokenClaims:
    """Return the claims if the caller is an admin. [4.3] #15

    Raises NotAnAdministrator. Deliberately does not log the read. ADR 0006.
    """
    if not claims.is_admin:
        raise NotAnAdministrator
    return claims


CurrentAdmin = Annotated[TokenClaims, Depends(require_admin)]
