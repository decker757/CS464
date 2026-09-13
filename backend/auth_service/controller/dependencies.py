"""FastAPI dependencies shared across routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core import security
from controller import transport
from core.database import get_session
from core.errors import AccountSuspended, InvalidToken
from model.entities import User

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
