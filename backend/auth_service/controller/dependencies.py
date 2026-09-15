"""FastAPI dependencies shared across routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core import security
from controller import transport
from core.database import get_session
from core.errors import AccountSuspended, InvalidToken, NotAnAdministrator
from core.roles import UserRole
from model.entities import User
from service.audit import Actor

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, session: DbSession) -> User:
    """[A-3] #31. Guards this service's own protected routes.

    Scoped to /auth/me. Other services do NOT import this; they verify the JWT
    signature themselves and never need our database. Sharing the verification
    helper is a separate deliverable, not part of this service.
    """
    token = transport.extract_access_token(request)
    if token is None:
        raise InvalidToken

    claims = security.decode_access_token(token)
    if claims is None:
        raise InvalidToken

    user = await session.get(User, claims.user_id)
    if user is None:
        raise InvalidToken

    if user.is_suspended:
        raise AccountSuspended

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    """Guard every administrative route. [4.4] #16.

    Note what this does NOT share with `market_service`'s guard of the same
    name. That service reads the role out of the signed token, because it holds
    no copy of the user table and cannot look one up; ADR 0003 records the
    consequence, that a demotion there takes up to one access-token lifetime to
    bite.

    This service owns the row. `get_current_user` has already loaded it, so the
    role checked here is the live one and a demotion binds these routes on the
    very next request. The token is used to say who the caller is, never what
    they may do.
    """
    if user.role is not UserRole.ADMIN:
        raise NotAnAdministrator
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]


async def get_actor(user: CurrentAdmin) -> Actor:
    """Narrow the caller to what the service layer is allowed to know.

    The audit log has to name who acted, and `service/audit.py` takes an
    `Actor` rather than a `User` so that it stays identical to the market
    service's copy and so that nothing below the controller depends on how the
    caller was identified. [4.3] #15.
    """
    return Actor(id=user.id, username=user.username, role=user.role.value)


CurrentActor = Annotated[Actor, Depends(get_actor)]
