"""FastAPI dependencies shared across routes.

The same shape as the market and audit services', and for the same three
reasons: a dependency defaults to closed where middleware defaults to open,
FastAPI renders it into /docs so the contract states which routes need a token,
and it hands the route typed claims rather than smuggling them through
request.state.

This service never queries auth.users. It cannot — there is no grant — and it
does not need to: a ledger account is created on demand for whatever user id a
signed token carries. The consequence is the one ADR 0003 records. A suspended
or deleted account keeps whatever authority its last access token carried, for
at most one access-token lifetime, which here means it can read its own balance
for fifteen minutes after being suspended. It cannot spend anything, because
there is no route that spends.
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


async def get_access_token(request: Request, _claims: CurrentUser) -> str:
    """The raw bearer token, for forwarding upstream. [T-1] #21, D-037.

    `CurrentUser` decodes this same token into claims; this reads it a second
    time as the string it arrived as, because a decoded claim cannot be
    re-signed into the credential `service/market_terms.py` forwards to
    market_service on a market's first touch. A token minted here instead
    would be this service asserting an identity it was not given.

    Depends on `CurrentUser` so the token has already been verified by the
    time this returns it — an unverified token must never be forwarded to
    another service. The `None` case below is therefore unreachable: it is
    the same header or cookie `CurrentUser` just required to exist.
    """
    token = transport.extract_access_token(request)
    if token is None:
        raise NotAuthenticated
    return token


# Depends on `CurrentUser` through `get_access_token`'s own signature, so
# verification always runs first: no route can receive this token unverified.
AccessToken = Annotated[str, Depends(get_access_token)]


async def require_admin(claims: CurrentUser) -> TokenClaims:
    """Guard the routes that read somebody else's money. [4.1] #13.

    The `/me` routes need no such guard, because the user id they read is the
    one in the token and a caller cannot ask about an account that is not
    theirs. That asymmetry is why there are two pairs of routes rather than one
    pair with a user id and an `if` inside: the authorisation rule lives in the
    signature, where FastAPI puts it in /docs and where it cannot be forgotten
    by an early return.

    Any role this build does not recognise has already been downgraded to
    TRADER by `security._read_role`, so a new role appearing during a rollout
    cannot let anybody in early.
    """
    if not claims.is_admin:
        raise NotAnAdministrator
    return claims


CurrentAdmin = Annotated[TokenClaims, Depends(require_admin)]
