"""FastAPI dependencies shared across routes: the session, who the caller is,
and the paging parameters.

Authentication is a dependency, not middleware: it is opt-in per route, shows
in /docs, and hands routes typed claims. This service never reads auth.users,
so a suspended account keeps its authority until its token expires. ADR 0003.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from controller import transport
from core import security
from core.database import get_session
from core.errors import NotAnAdministrator, NotAuthenticated
from core.security import TokenClaims
from service.audit import Actor

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_claims(request: Request) -> TokenClaims:
    """Verify the token and return its claims; raises `NotAuthenticated`. No database."""
    token = transport.extract_access_token(request)
    if token is None:
        raise NotAuthenticated

    claims = security.decode_access_token(token)
    if claims is None:
        raise NotAuthenticated

    return claims


CurrentUser = Annotated[TokenClaims, Depends(get_claims)]


async def require_admin(claims: CurrentUser) -> TokenClaims:
    """Guard every market-authoring route; raises `NotAnAdministrator`. [1.1] #1.

    The role comes from the signed token only, and an unknown role has
    already been read as TRADER.
    """
    if not claims.is_admin:
        raise NotAnAdministrator
    return claims


CurrentAdmin = Annotated[TokenClaims, Depends(require_admin)]


async def get_actor(claims: CurrentAdmin) -> Actor:
    """Narrow verified admin claims to the `Actor` the audit log names. [4.3] #15.

    Here, not in `service/`, so nothing below the controller knows a JWT was
    involved.
    """
    return Actor(id=claims.user_id, username=claims.username, role=claims.role.value)


CurrentActor = Annotated[Actor, Depends(get_actor)]


# The two parameters of every keyset-paged list here. [X-1] #104, #210.
PageLimit = Annotated[
    int | None,
    Query(
        ge=1,
        description=(
            "Markets per page. Defaults to the server's page size and is "
            "capped by its maximum: a larger value is clamped, not refused."
        ),
    ),
]
PageCursor = Annotated[
    str | None,
    Query(description="The `next_cursor` from the previous page, unmodified."),
]
