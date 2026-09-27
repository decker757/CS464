"""[A-3] #31 token issuance, rotation and revocation, below the HTTP layer."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import InvalidToken
from core.security import hash_refresh_token
from model.entities import RefreshToken, User
from model.schemas import TokenPair
from service import auth_service


async def _live_token_count(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None))
    )
    return (await session.execute(stmt)).scalar_one()


async def _wait_briefly(event: asyncio.Event) -> None:
    """Wait for the other party, but never long enough to hang a fixed build."""
    with suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=1)


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


async def test_two_concurrent_refreshes_of_one_token_leave_one_winner_and_no_session(
    session: AsyncSession, registered_user: User
) -> None:
    """The client and whoever copied its cookie, at once. ADR 0015.

    The second waits on the row lock, re-reads the revocation, and is the replay.
    """
    tokens = await auth_service.issue_tokens(session, registered_user)
    await session.commit()
    factory = get_session_factory()

    # Both connected first, so the lock decides the outcome. ADR 0015.
    barrier = asyncio.Barrier(2)

    async def refresh() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await auth_service.rotate_refresh_token(own, tokens.refresh_token)
            except InvalidToken:
                return "refused"
            return "issued"

    results = await asyncio.gather(refresh(), refresh())

    assert sorted(results) == ["issued", "refused"]
    assert await _live_token_count(session) == 0


async def test_a_replay_revokes_a_session_rotated_at_the_same_moment(
    session: AsyncSession, registered_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replay racing the attacker's own rotation still ends every session. #137

    The hooks hold the rotation's new token flushed but uncommitted while the
    replay reads the live tokens, so without the user-row lock that read misses
    it every time. With the lock the replay waits for the commit instead, and
    the hooks time out.
    """
    replayed = await auth_service.issue_tokens(session, registered_user)
    attacker = await auth_service.issue_tokens(session, registered_user)
    await session.commit()
    await auth_service.rotate_refresh_token(session, replayed.refresh_token)

    rotation_pending = asyncio.Event()
    replay_has_read = asyncio.Event()
    real_issue_tokens = auth_service.issue_tokens
    real_revoke_all = auth_service.revoke_all_for_user

    async def issue_and_hold_uncommitted(own: AsyncSession, user: User) -> TokenPair:
        pair = await real_issue_tokens(own, user)
        await own.flush()
        rotation_pending.set()
        await _wait_briefly(replay_has_read)
        return pair

    async def revoke_all_while_rotation_pending(
        own: AsyncSession, user_id: uuid.UUID
    ) -> None:
        await _wait_briefly(rotation_pending)
        await real_revoke_all(own, user_id)
        replay_has_read.set()

    monkeypatch.setattr(auth_service, "issue_tokens", issue_and_hold_uncommitted)
    monkeypatch.setattr(
        auth_service, "revoke_all_for_user", revoke_all_while_rotation_pending
    )

    factory = get_session_factory()
    # Both connected first, so the lock decides the outcome. ADR 0015.
    barrier = asyncio.Barrier(2)

    async def refresh(raw: str) -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await auth_service.rotate_refresh_token(own, raw)
            except InvalidToken:
                return "refused"
            return "issued"

    # The attacker's rotation may win or be refused, depending on who takes
    # the user row first; either way no session may survive.
    _, replay = await asyncio.gather(
        refresh(attacker.refresh_token), refresh(replayed.refresh_token)
    )

    assert replay == "refused"
    assert await _live_token_count(session) == 0
