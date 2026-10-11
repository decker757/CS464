"""Building and counting user rows in service tests.

Shared by `test_role_changes.py`, `test_sessions.py` and `test_suspensions.py`.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from model.entities import RefreshToken, User


def admin_row(username: str, *, suspended: bool = False) -> User:
    """An administrator row with a placeholder hash; nothing here logs in."""
    return User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
        is_suspended=suspended,
    )


async def live_token_count(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None))
    )
    return (await session.execute(stmt)).scalar_one()
