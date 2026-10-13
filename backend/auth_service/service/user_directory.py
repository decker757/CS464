"""Naming accounts for another service. [L-1] #38, ADR 0021

A read with no lock and no audit entry: the log records decisions, and this
decides nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from model.entities import User


async def list_by_ids(session: AsyncSession, user_ids: Sequence[uuid.UUID]) -> list[User]:
    """The users among `user_ids`, ordered by `id`.

    A duplicate collapses in the `IN`, and an id that names no user is simply
    absent: the caller decides what a missing user means.
    """
    # `raiseload`, for `get_current_user`'s reason: `lazy="selectin"` would
    # load every refresh token of up to 500 users, and nothing here reads them.
    stmt = (
        select(User)
        .where(User.id.in_(user_ids))
        .order_by(User.id)
        .options(raiseload(User.refresh_tokens))
    )
    return list((await session.execute(stmt)).scalars())
