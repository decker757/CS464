"""FastAPI dependencies shared across routes.

This is the auth middleware the ticket asks about, expressed as a dependency
rather than as middleware. Three reasons:

- Middleware runs on every request, including /health and /docs, so it would
  have to carry a list of paths to skip, and a route added later is
  unprotected by default. A dependency is opt-in per route and defaults to
  closed.
- FastAPI renders a dependency into the OpenAPI document, so /docs states which
  routes need a token. Michelle reads that page as the contract.
- It hands the route typed claims. Middleware would have to smuggle them
  through request.state, untyped.

If an API gateway later terminates authentication, this file is the only thing
that changes: the gateway would inject a trusted, signed header and
`get_claims` would read that instead. Nothing in `service/` moves, because
nothing in `service/` knows where the caller's id came from.

This service never queries auth.users. It cannot — there is no grant — and it
should not want to. The consequence is real and is recorded in ADR 0003: a
suspended or deleted account keeps whatever authority its last access token
carried, for at most one access-token lifetime.
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
    """Guard every market-authoring route. [1.1] #1.

    The role is read from the signed token, never from the request body or a
    header the client controls. Any role this build does not recognise has
    already been downgraded to TRADER by `security._read_role`, so a new role
    appearing during a rollout cannot let anybody in early.
    """
    if not claims.is_admin:
        raise NotAnAdministrator
    return claims


CurrentAdmin = Annotated[TokenClaims, Depends(require_admin)]
