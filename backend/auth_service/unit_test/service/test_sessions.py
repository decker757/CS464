"""[A-3] #31 token issuance, rotation and revocation, below the HTTP layer."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import InvalidToken
from core.security import hash_refresh_token
from model.entities import RefreshToken, User
from service import auth_service


async def _live_token_count(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None))
    )
    return (await session.execute(stmt)).scalar_one()


async def test_issuing_tokens_persists_only_the_hash(
    session: AsyncSession, registered_user: User
) -> None:
    tokens = await auth_service.issue_tokens(session, registered_user)
    await session.commit()

    stored = (
        await session.execute(
            select(RefreshToken.token_hash).where(
                RefreshToken.token_hash == hash_refresh_token(tokens.refresh_token)
            )
        )
    ).scalar_one()

    assert stored != tokens.refresh_token
    assert len(stored) == 64


async def test_rotation_revokes_the_presented_token(
    session: AsyncSession, registered_user: User
) -> None:
    first = await auth_service.issue_tokens(session, registered_user)
    await session.commit()

    _, rotated = await auth_service.rotate_refresh_token(session, first.refresh_token)

    assert rotated.refresh_token != first.refresh_token
    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, first.refresh_token)


async def test_an_unknown_refresh_token_is_refused(
    session: AsyncSession, registered_user: User
) -> None:
    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, "never-issued-by-us")


async def test_replaying_a_revoked_token_kills_every_live_session(
    session: AsyncSession, registered_user: User
) -> None:
    """A replay means the token leaked, so all outstanding sessions are cut."""
    stolen = await auth_service.issue_tokens(session, registered_user)
    await session.commit()

    await auth_service.rotate_refresh_token(session, stolen.refresh_token)
    assert await _live_token_count(session) >= 1

    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, stolen.refresh_token)

    assert await _live_token_count(session) == 0


async def test_revoking_is_idempotent_and_silent_when_unknown(
    session: AsyncSession, registered_user: User
) -> None:
    tokens = await auth_service.issue_tokens(session, registered_user)
    await session.commit()

    await auth_service.revoke_refresh_token(session, tokens.refresh_token)
    await auth_service.revoke_refresh_token(session, tokens.refresh_token)
    await auth_service.revoke_refresh_token(session, "never-issued-by-us")

    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, tokens.refresh_token)


async def test_a_suspended_user_cannot_refresh(
    session: AsyncSession, registered_user: User
) -> None:
    tokens = await auth_service.issue_tokens(session, registered_user)
    registered_user.is_suspended = True
    await session.commit()

    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, tokens.refresh_token)
