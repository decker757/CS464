"""FastAPI dependencies shared across routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from controller import transport
from core import security
from core.database import get_session
from core.errors import AccountSuspended, InvalidToken, NotAnAdministrator
from core.roles import UserRole
from model.entities import User
from service.audit import Actor

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, session: DbSession) -> User:
    """Return the caller's row, loaded from the access token's subject. [A-3] #31

    Raises InvalidToken, or AccountSuspended. Other services verify the token
    themselves and never read this database.
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
    """Return the caller if their row says admin. [4.4] #16

    Raises NotAnAdministrator. Reads the live row, not the token's claim, so a
    demotion binds here on the next request. ADR 0007.
    """
    if user.role is not UserRole.ADMIN:
        raise NotAnAdministrator
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]


async def get_actor(user: CurrentAdmin) -> Actor:
    """Return the admin caller as the `Actor` the audit log names. [4.3] #15

    An `Actor`, not a `User`, so `service/audit.py` stays identical to the
    market service's copy.
    """
    return Actor(id=user.id, username=user.username, role=user.role.value)


CurrentActor = Annotated[Actor, Depends(get_actor)]
