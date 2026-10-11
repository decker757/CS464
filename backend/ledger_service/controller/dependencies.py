"""FastAPI dependencies shared across routes.

Dependencies rather than middleware: they default to closed, and /docs shows
which routes need a token. Authority comes from the signed token alone, so a
suspended user keeps it for one access-token lifetime (ADR 0003).
"""

from __future__ import annotations

from typing import Annotated

import httpx
import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from controller import transport
from core import security
from core.database import get_session
from core.errors import NotAnAdministrator, NotAuthenticated
from core.security import TokenClaims
from service.audit import Actor, actor_of

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_redis(request: Request) -> redis.Redis:
    """The process-wide Redis client `main.py`'s lifespan built (D-047)."""
    return request.app.state.redis


RedisClient = Annotated[redis.Redis, Depends(get_redis)]


async def get_terms_client(request: Request) -> httpx.AsyncClient:
    """The process-wide market_service client `main.py`'s lifespan built (#114)."""
    return request.app.state.terms_client


TermsClient = Annotated[httpx.AsyncClient, Depends(get_terms_client)]


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
    """The raw bearer token, for forwarding to market_service. D-018, D-037.

    A decoded claim cannot be re-signed, and a token minted here would assert
    an identity this service was not given. Depends on `CurrentUser`, so an
    unverified token is never forwarded; the `None` case is unreachable.
    """
    token = transport.extract_access_token(request)
    if token is None:
        raise NotAuthenticated
    return token


AccessToken = Annotated[str, Depends(get_access_token)]


async def require_admin(claims: CurrentUser) -> TokenClaims:
    """Guard the routes that read somebody else's money. [4.1] #13.

    The `/me` routes need none: they read the token's own user id. A role this
    build does not recognise is already TRADER (`security._read_role`).
    """
    if not claims.is_admin:
        raise NotAnAdministrator
    return claims


CurrentAdmin = Annotated[TokenClaims, Depends(require_admin)]


async def get_actor(claims: CurrentAdmin) -> Actor:
    """Narrow verified admin claims to the `Actor` the audit log names. [3.4] #12.

    Here, not in `service/`, so nothing below the controller knows a JWT was
    involved. Built on `CurrentAdmin`: the settlement route has no other
    admin check, so this dependency is what refuses a trader.
    """
    return actor_of(claims)


CurrentActor = Annotated[Actor, Depends(get_actor)]
