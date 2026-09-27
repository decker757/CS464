"""The rules for changing somebody else's role, without HTTP. [4.4] #16

"Only an administrator may call this" is the controller's guard, tested there.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import CannotChangeOwnRole, LastAdministrator, UserNotFound
from core.roles import UserRole
from model.entities import User
from service import auth_service, user_admin
from service.audit import Actor


def _actor(user_id: uuid.UUID | None = None) -> Actor:
    return Actor(id=user_id or uuid.uuid4(), username="ernest_t", role="admin")


def _admin(username: str, *, suspended: bool = False) -> User:
    """An administrator row with a placeholder hash; nothing here logs in."""
    return User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
        is_suspended=suspended,
    )


async def _reloaded(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """The user as committed, not as this session last saw them."""
    session.expunge_all()
    return await session.get(User, user_id)


async def _demote_each_other(first: uuid.UUID, second: uuid.UUID) -> list[str]:
    """Two administrators demote each other in two concurrent transactions.

    Returns each side's outcome, "applied" or "refused".
    """
    factory = get_session_factory()
    # Both sessions connect before either starts, so the lock decides the
    # outcome and not which session opened its connection first. ADR 0015.
    both_connected = asyncio.Barrier(2)

    async def demote(actor_id: uuid.UUID, target_id: uuid.UUID) -> str:
        async with factory() as own_session:
            await own_session.connection()
            await both_connected.wait()
            try:
                await user_admin.change_role(
                    own_session,
                    actor=_actor(actor_id),
                    target_id=target_id,
                    role=UserRole.TRADER,
                )
            except LastAdministrator:
                return "refused"
            return "applied"

    outcomes = await asyncio.gather(demote(first, second), demote(second, first))
    return list(outcomes)


# --- the change itself ----------------------------------------------------
async def test_the_change_survives_the_transaction(
    session: AsyncSession, registered_user: User
) -> None:
    """Committed, not merely set on an in-memory object."""
    await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    reloaded = await _reloaded(session, registered_user.id)

    assert reloaded is not None
    assert reloaded.role is UserRole.ADMIN


async def test_the_new_role_reaches_the_next_access_token(
    session: AsyncSession, registered_user: User
) -> None:
    """Other services learn the role only from the token. ADR 0003."""
    from core import security  # noqa: PLC0415

    await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    pair = await auth_service.issue_tokens(session, registered_user)
    claims = security.decode_access_token(pair.access_token)

    assert claims is not None
    assert claims.role is UserRole.ADMIN


# --- what is refused ------------------------------------------------------
async def test_a_refused_self_change_leaves_the_role_alone(
    session: AsyncSession, registered_user: User
) -> None:
    """One of the two rules that keep an administrator in the database. ADR 0007.

    With one administrator, the only caller may not target themselves.
    """
    registered_user.role = UserRole.ADMIN
    await session.commit()

    with pytest.raises(CannotChangeOwnRole):
        await user_admin.change_role(
            session,
            actor=_actor(registered_user.id),
            target_id=registered_user.id,
            role=UserRole.TRADER,
        )

    reloaded = await _reloaded(session, registered_user.id)
    assert reloaded is not None
    assert reloaded.role is UserRole.ADMIN


async def test_an_unknown_target_is_refused(session: AsyncSession) -> None:
    with pytest.raises(UserNotFound):
        await user_admin.change_role(
            session, actor=_actor(), target_id=uuid.uuid4(), role=UserRole.ADMIN
        )


# --- idempotence ----------------------------------------------------------
async def test_promoting_twice_is_the_same_as_promoting_once(
    session: AsyncSession, registered_user: User
) -> None:
    for _ in range(2):
        await user_admin.change_role(
            session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
        )

    reloaded = await _reloaded(session, registered_user.id)
    assert reloaded is not None
    assert reloaded.role is UserRole.ADMIN


# --- the last administrator ----------------------------------------------
async def test_the_only_administrator_cannot_be_demoted_by_anyone(
    session: AsyncSession, registered_user: User
) -> None:
    """The gap the self-change rule leaves, reachable by a race. ADR 0007."""
    registered_user.role = UserRole.ADMIN
    await session.commit()

    with pytest.raises(LastAdministrator):
        await user_admin.change_role(
            session, actor=_actor(), target_id=registered_user.id, role=UserRole.TRADER
        )


async def test_an_administrator_can_be_demoted_while_another_remains(
    session: AsyncSession, registered_user: User
) -> None:
    """The guard is about the last one, not about demotion in general."""
    session.add(_admin("admin_two"))
    registered_user.role = UserRole.ADMIN
    await session.commit()

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.TRADER
    )

    assert updated.role is UserRole.TRADER


async def test_two_administrators_demoting_each_other_leaves_one(
    session: AsyncSession, registered_user: User
) -> None:
    """The race the self-change rule cannot see, run for real. ADR 0007.

    Asserts the outcome, not the mechanism: exactly one wins, either one.
    """
    other = _admin("admin_two")
    session.add(other)
    registered_user.role = UserRole.ADMIN
    await session.commit()

    outcomes = await _demote_each_other(registered_user.id, other.id)

    assert sorted(outcomes) == ["applied", "refused"]

    session.expunge_all()
    survivors = [
        user.id
        for user in (await session.execute(select(User))).scalars()
        if user.role is UserRole.ADMIN
    ]
    assert len(survivors) == 1


# --- suspension is a separate axis ----------------------------------------
async def test_a_suspended_user_can_still_have_their_role_changed(
    session: AsyncSession, registered_user: User
) -> None:
    """Suspension and role are independent axes, deliberately."""
    registered_user.is_suspended = True
    await session.commit()

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    assert updated.role is UserRole.ADMIN
    assert updated.is_suspended is True


# --- a suspended administrator is not an administrator --------------------
async def test_a_suspended_administrator_does_not_count_toward_the_last_one(
    session: AsyncSession, registered_user: User
) -> None:
    """The guard counts who can act, not who the column calls admin. ADR 0007."""
    session.add(_admin("admin_ghost", suspended=True))
    registered_user.role = UserRole.ADMIN
    await session.commit()

    with pytest.raises(LastAdministrator):
        await user_admin.change_role(
            session, actor=_actor(), target_id=registered_user.id, role=UserRole.TRADER
        )


async def test_a_suspended_administrator_can_still_be_demoted(
    session: AsyncSession, registered_user: User
) -> None:
    """Excluded from the count, but still a legal target. ADR 0007."""
    suspended = _admin("admin_ghost", suspended=True)
    session.add(suspended)
    registered_user.role = UserRole.ADMIN
    await session.commit()

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=suspended.id, role=UserRole.TRADER
    )

    assert updated.role is UserRole.TRADER
    assert updated.is_suspended is True


async def test_a_suspended_administrator_does_not_save_a_mutual_demotion(
    session: AsyncSession, registered_user: User
) -> None:
    """The race again, with a suspended administrator who must not count."""
    other = _admin("admin_two")
    session.add_all([_admin("admin_ghost", suspended=True), other])
    registered_user.role = UserRole.ADMIN
    await session.commit()

    outcomes = await _demote_each_other(registered_user.id, other.id)
    assert sorted(outcomes) == ["applied", "refused"]

    session.expunge_all()
    usable = [
        user.id
        for user in (await session.execute(select(User))).scalars()
        if user.role is UserRole.ADMIN and not user.is_suspended
    ]
    assert len(usable) == 1
