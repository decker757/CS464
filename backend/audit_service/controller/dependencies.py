"""FastAPI dependencies shared across routes.

The same shape as the market service's, and for the same three reasons: a
dependency defaults to closed where middleware defaults to open, FastAPI
renders it into /docs so the contract states which routes need a token, and it
hands the route typed claims rather than smuggling them through request.state.

This service never queries auth.users either. It cannot — there is no grant —
and it does not need to, because every name it shows was snapshotted into the
row when the action happened.
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
    """Verify the signature and return who the caller is.

    No database access at all. The token is self-contained and this service
    holds no copy of the user table to check it against.
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
    """Guard the whole log. [4.3] #15.

    Reading the log is an administrator's privilege, not a public one: it
    records what administrators did to other people's accounts and markets, so
    the feed names accounts and quotes reasons a trader has no business
    reading.

    Note what this does NOT do: log the read. Every entry here is an action
    that changed something, and mixing "ernest looked at the log" into the same
    table would bury the writes under the reads within a week. Read auditing is
    a different feature with a different retention story.
    """
    if not claims.is_admin:
        raise NotAnAdministrator
    return claims


CurrentAdmin = Annotated[TokenClaims, Depends(require_admin)]
