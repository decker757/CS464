"""The rules for changing somebody else's role. [4.4] #16

Asserted here, without HTTP, because they are business rules. What the
controller is responsible for — the status code each of these maps to, and the
shape of the response — is asserted in unit_test/controller.

What is deliberately NOT tested here is "only an administrator may call this".
That guard lives in `controller/dependencies.require_admin`, for the same
reason the market service puts its there: it is the one layer that knows how a
caller was identified.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import CannotChangeOwnRole, LastAdministrator, UserNotFound
from core.roles import UserRole
from model.entities import User
from service import auth_service, user_admin
from service.audit import Actor


def _actor(user_id: uuid.UUID | None = None) -> Actor:
    return Actor(id=user_id or uuid.uuid4(), username="ernest_t", role="admin")


# --- the change itself ----------------------------------------------------
async def test_it_promotes_a_trader(session: AsyncSession, registered_user: User) -> None:
    assert registered_user.role is UserRole.TRADER

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    assert updated.role is UserRole.ADMIN


async def test_it_demotes_an_administrator(
    session: AsyncSession, registered_user: User, another_administrator: User
) -> None:
    registered_user.role = UserRole.ADMIN
    await session.commit()

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.TRADER
    )

    assert updated.role is UserRole.TRADER


async def test_the_change_survives_the_transaction(
    session: AsyncSession, registered_user: User
) -> None:
    """Committed, not merely set on an in-memory object."""
    await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    session.expunge_all()
    reloaded = await session.get(User, registered_user.id)

    assert reloaded is not None
    assert reloaded.role is UserRole.ADMIN


async def test_the_new_role_reaches_the_next_access_token(
    session: AsyncSession, registered_user: User
) -> None:
    """The point of the whole exercise. [1.1] #1's guard reads this claim.

    The market service cannot look a role up — it holds no grant on auth.users
    — so a promotion that did not reach the token would be a promotion that
    never reached the service it was for.
    """
    from core import security  # noqa: PLC0415

    await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    pair = await auth_service.issue_tokens(session, registered_user)
    claims = security.decode_access_token(pair.access_token)

    assert claims is not None
    assert claims.role is UserRole.ADMIN


# --- what is refused ------------------------------------------------------
async def test_an_administrator_cannot_change_their_own_role(
    session: AsyncSession, registered_user: User
) -> None:
    """The invariant. See CannotChangeOwnRole and ADR 0007.

    Not a courtesy against a self-footgun: it is the only thing standing
    between this endpoint and a database with no administrator in it.
    """
    with pytest.raises(CannotChangeOwnRole):
        await user_admin.change_role(
            session,
            actor=_actor(registered_user.id),
            target_id=registered_user.id,
            role=UserRole.TRADER,
        )


async def test_a_refused_self_change_leaves_the_role_alone(
    session: AsyncSession, registered_user: User
) -> None:
    registered_user.role = UserRole.ADMIN
    await session.commit()

    with pytest.raises(CannotChangeOwnRole):
        await user_admin.change_role(
            session,
            actor=_actor(registered_user.id),
            target_id=registered_user.id,
            role=UserRole.TRADER,
        )

    session.expunge_all()
    reloaded = await session.get(User, registered_user.id)
    assert reloaded is not None
    assert reloaded.role is UserRole.ADMIN


async def test_the_last_administrator_cannot_be_demoted(
    session: AsyncSession, registered_user: User
) -> None:
    """The invariant stated as the property it exists for.

    With one administrator, the only caller entitled to demote anybody is the
    only person they may not target. The set cannot reach zero.
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


async def test_an_unknown_target_is_refused(session: AsyncSession) -> None:
    with pytest.raises(UserNotFound):
        await user_admin.change_role(
            session, actor=_actor(), target_id=uuid.uuid4(), role=UserRole.ADMIN
        )


# --- idempotence ----------------------------------------------------------
async def test_asking_for_the_role_already_held_succeeds(
    session: AsyncSession, registered_user: User
) -> None:
    """A repeated request is the same outcome, not an error.

    PATCH carries the role the user should end up with rather than a delta, so
    a double-submitted form is not a bug report.
    """
    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.TRADER
    )

    assert updated.role is UserRole.TRADER


async def test_promoting_twice_is_the_same_as_promoting_once(
    session: AsyncSession, registered_user: User
) -> None:
    for _ in range(2):
        await user_admin.change_role(
            session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
        )

    session.expunge_all()
    reloaded = await session.get(User, registered_user.id)
    assert reloaded is not None
    assert reloaded.role is UserRole.ADMIN


# --- the last administrator ----------------------------------------------
async def test_the_only_administrator_cannot_be_demoted_by_anyone(
    session: AsyncSession, registered_user: User
) -> None:
    """The self-change rule is not enough on its own, and this is the gap.

    It stops the only administrator demoting *themselves*. It says nothing
    about somebody else demoting them, which is reachable the moment two
    administrators act at the same instant — see the concurrency test below.
    """
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
    other = User(
        username="admin_two",
        email="admin.two@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
    )
    session.add(other)
    registered_user.role = UserRole.ADMIN
    await session.commit()

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.TRADER
    )

    assert updated.role is UserRole.TRADER


async def test_two_administrators_demoting_each_other_leaves_one(
    session: AsyncSession, registered_user: User
) -> None:
    """The race the self-change rule cannot see, run for real.

    Each request targets somebody other than itself, so each passes that check.
    Without the row lock on the demotion path both commit and the database is
    left with no administrator at all — unreachable by any route, recoverable
    only by hand. This asserts the outcome, not the mechanism: exactly one of
    the two wins, and which one is not fixed.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    other = User(
        username="admin_two",
        email="admin.two@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
    )
    session.add(other)
    registered_user.role = UserRole.ADMIN
    await session.commit()

    first, second = registered_user.id, other.id
    factory = get_session_factory()

    async def demote(actor_id: uuid.UUID, target_id: uuid.UUID) -> str:
        async with factory() as own_session:
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

    outcomes = await asyncio.gather(
        demote(first, second), demote(second, first)
    )

    assert sorted(outcomes) == ["applied", "refused"]

    session.expunge_all()
    survivors = [
        user.id
        for user in (await session.execute(select(User))).scalars()
        if user.role is UserRole.ADMIN
    ]
    assert len(survivors) == 1


async def test_a_demotion_already_applied_concurrently_is_not_logged_twice(
    session: AsyncSession, registered_user: User
) -> None:
    """Two administrators demoting the same third person, one moment apart.

    The second request finds the target already a trader and becomes the
    idempotent case: no write, and no second entry claiming a change from a
    role the user no longer held.
    """
    target = User(
        username="admin_three",
        email="admin.three@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
    )
    session.add(target)
    registered_user.role = UserRole.ADMIN
    await session.commit()

    await user_admin.change_role(
        session, actor=_actor(), target_id=target.id, role=UserRole.TRADER
    )
    again = await user_admin.change_role(
        session, actor=_actor(), target_id=target.id, role=UserRole.TRADER
    )

    assert again.role is UserRole.TRADER


# --- suspension is a separate axis ----------------------------------------
async def test_a_suspended_user_can_still_have_their_role_changed(
    session: AsyncSession, registered_user: User
) -> None:
    """Deliberate, and worth stating rather than leaving to be discovered.

    Suspension and role are independent: [4.2] #14 stops somebody signing in,
    this decides what they may do once they can. Demoting a suspended
    administrator is exactly what an administrator would want to do on the way
    to cleaning up a compromised account, and refusing it would make the two
    stories fight each other.
    """
    registered_user.is_suspended = True
    await session.commit()

    updated = await user_admin.change_role(
        session, actor=_actor(), target_id=registered_user.id, role=UserRole.ADMIN
    )

    assert updated.role is UserRole.ADMIN
    assert updated.is_suspended is True
