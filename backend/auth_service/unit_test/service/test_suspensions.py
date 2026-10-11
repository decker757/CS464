"""Suspending and unsuspending somebody else's account, without HTTP. [4.2] #14

"Only an administrator may call this" is the controller's guard, tested there.
The audit entries are in `test_audit.py`, beside the role change's.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import (
    AccountSuspended,
    CannotSuspendSelf,
    InvalidToken,
    LastAdministrator,
    SuspensionIncomplete,
    UserNotFound,
)
from core.roles import UserRole
from model.entities import User
from model.schemas import TokenPair
from service import auth_service, user_admin
from service.audit import Actor
from unit_test.conftest import VALID_PASSWORD
from unit_test.service.lock_waits import wait_briefly, wait_until_blocked_on_a_lock
from unit_test.service.user_rows import admin_row, live_token_count

REASON = "Second account of bob_1, same device and same card."


def _actor(user_id: uuid.UUID | None = None) -> Actor:
    return Actor(id=user_id or uuid.uuid4(), username="ihsan_b", role="admin")


async def _suspend(
    session: AsyncSession,
    target_id: uuid.UUID,
    *,
    actor: Actor | None = None,
    suspended: bool = True,
    reason: str = REASON,
) -> User:
    return await user_admin.set_suspension(
        session,
        actor=actor or _actor(),
        target_id=target_id,
        suspended=suspended,
        reason=reason,
    )


async def _is_suspended(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """As committed, read as a column so the identity map cannot answer."""
    stmt = select(User.is_suspended).where(User.id == user_id)
    return (await session.execute(stmt)).scalar_one()


async def _active_administrators(session: AsyncSession) -> int:
    stmt = select(func.count()).where(
        User.role == UserRole.ADMIN, User.is_suspended.is_(False)
    )
    return (await session.execute(stmt)).scalar_one()


# --- the main flow --------------------------------------------------------
async def test_a_suspended_user_cannot_log_in(
    session: AsyncSession, registered_user: User
) -> None:
    """[4.2] #14: suspension blocks login."""
    await _suspend(session, registered_user.id)

    with pytest.raises(AccountSuspended):
        await auth_service.authenticate(session, "ernest_t", VALID_PASSWORD)


async def test_a_session_from_before_the_suspension_stays_dead_after_unsuspension(
    session: AsyncSession, registered_user: User
) -> None:
    """Refresh refuses a suspended user anyway; the revoke is what keeps an old
    cookie dead once the account is unsuspended. DECISIONS.md, "Suspending
    revokes every refresh token the account holds"."""
    before = await auth_service.issue_tokens(session, registered_user)
    await session.commit()

    await _suspend(session, registered_user.id)
    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, before.refresh_token)
    await session.rollback()

    await _suspend(session, registered_user.id, suspended=False)
    with pytest.raises(InvalidToken):
        await auth_service.rotate_refresh_token(session, before.refresh_token)


@pytest.mark.parametrize("role", [UserRole.TRADER, UserRole.ADMIN])
async def test_a_suspended_user_can_log_in_again_once_unsuspended(
    session: AsyncSession,
    registered_user: User,
    another_administrator: User,
    role: UserRole,
) -> None:
    """[4.2] #14: reversible. The admin case is the trap in the issue's comment:
    a suspended administrator is absent from the locked set and must still be
    a valid target."""
    registered_user.role = role
    await session.commit()
    await _suspend(session, registered_user.id)

    await _suspend(session, registered_user.id, suspended=False)

    found = await auth_service.authenticate(session, "ernest_t", VALID_PASSWORD)
    assert found.id == registered_user.id
    assert await _is_suspended(session, registered_user.id) is False


# --- the reason -----------------------------------------------------------
@pytest.mark.parametrize("suspended", [True, False])
@pytest.mark.parametrize(
    "reason", ["", "x" * 9, "  " + "x" * 9 + "  "], ids=repr
)
async def test_a_reason_shorter_than_ten_characters_is_refused(
    session: AsyncSession, registered_user: User, suspended: bool, reason: str
) -> None:
    """[4.2] #14: requires a reason, both ways. Counted after trimming."""
    registered_user.is_suspended = not suspended
    await session.commit()
    target_id = registered_user.id

    with pytest.raises(SuspensionIncomplete) as refused:
        await _suspend(session, target_id, suspended=suspended, reason=reason)

    assert [problem.field for problem in refused.value.problems] == ["reason"]
    assert await _is_suspended(session, target_id) == (not suspended)


async def test_a_reason_of_ten_characters_once_trimmed_is_enough(
    session: AsyncSession, registered_user: User
) -> None:
    """Ten characters after trimming is enough: the floor's other edge."""
    await _suspend(session, registered_user.id, reason="  " + "x" * 10 + "  ")

    assert await _is_suspended(session, registered_user.id) is True


# --- what is refused ------------------------------------------------------
async def test_an_administrator_cannot_suspend_themselves(
    session: AsyncSession, registered_user: User
) -> None:
    """ADR 0007's identity rule, before any lookup."""
    registered_user.role = UserRole.ADMIN
    await session.commit()

    with pytest.raises(CannotSuspendSelf):
        await _suspend(
            session, registered_user.id, actor=_actor(registered_user.id)
        )

    assert await _is_suspended(session, registered_user.id) is False


async def test_an_unknown_target_is_refused(session: AsyncSession) -> None:
    with pytest.raises(UserNotFound):
        await _suspend(session, uuid.uuid4())


async def test_the_only_active_administrator_cannot_be_suspended(
    session: AsyncSession, registered_user: User
) -> None:
    """ADR 0007 and the #14 issue comment. The other administrator is already
    suspended, so this pins the guard and the set's predicate together."""
    session.add(admin_row("admin_ghost", suspended=True))
    registered_user.role = UserRole.ADMIN
    await session.commit()
    # Read before the rollback, which expires every instance.
    target_id = registered_user.id

    with pytest.raises(LastAdministrator):
        await _suspend(session, target_id)

    await session.rollback()
    assert await _is_suspended(session, target_id) is False


# --- races ----------------------------------------------------------------
async def test_two_administrators_suspending_each_other_leaves_one(
    session: AsyncSession, registered_user: User
) -> None:
    """The race the self rule cannot see. ADR 0007, ADR 0015.

    Both sessions connect, then wait on the Barrier, so the lock decides the
    outcome. Exactly one wins, either one.
    """
    other = admin_row("admin_two")
    session.add(other)
    registered_user.role = UserRole.ADMIN
    await session.commit()
    factory = get_session_factory()
    both_connected = asyncio.Barrier(2)

    async def suspend(actor_id: uuid.UUID, target_id: uuid.UUID) -> str:
        async with factory() as own:
            await own.connection()
            await both_connected.wait()
            try:
                await _suspend(own, target_id, actor=_actor(actor_id))
            except LastAdministrator:
                return "refused"
            return "applied"

    outcomes = await asyncio.gather(
        suspend(registered_user.id, other.id), suspend(other.id, registered_user.id)
    )

    assert sorted(outcomes) == ["applied", "refused"]
    assert await _active_administrators(session) == 1


async def test_a_suspension_waiting_on_a_demotion_leaves_one_administrator(
    session: AsyncSession, registered_user: User
) -> None:
    """ADR 0007: suspension "needs the same lock" as a demotion.

    B's demotion of A is held open after its set lock; A's suspension of B
    must queue behind it, and then find B the only administrator left. A
    Barrier alone would pass in one order with the lock removed, so the order
    is forced: the suspension starts only once the demotion holds the set.
    """
    admin_a = registered_user
    admin_b = admin_row("admin_two")
    session.add(admin_b)
    admin_a.role = UserRole.ADMIN
    await session.commit()
    factory = get_session_factory()

    async with factory() as demotion, factory() as suspension:
        suspension_pid = await suspension.scalar(text("SELECT pg_backend_pid()"))

        # `change_role` up to its commit, held open here.
        await user_admin._lock_administrators(demotion)
        demoted = await auth_service.lock_user(demotion, admin_a.id)
        demoted.role = UserRole.TRADER
        await demotion.flush()

        suspend = asyncio.create_task(
            _suspend(suspension, admin_b.id, actor=_actor(admin_a.id))
        )
        await wait_until_blocked_on_a_lock(demotion, suspension_pid)
        await demotion.commit()

        with pytest.raises(LastAdministrator):
            await suspend

    assert await _active_administrators(session) == 1


async def test_a_refresh_racing_a_suspension_keeps_no_session(
    session: AsyncSession, registered_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DECISIONS.md, "The refresh path locks the user row before the token":
    `revoke_all_for_user` needs the user's row lock, and suspension holds it.

    A trader, so only `lock_user` guards the row. The hooks hold the rotation's
    new token flushed but uncommitted while the suspension revokes, so without
    the lock that revoke misses it every time. With the lock the suspension
    waits for the commit instead, and the hooks time out.
    """
    tokens = await auth_service.issue_tokens(session, registered_user)
    await session.commit()

    rotation_pending = asyncio.Event()
    suspension_has_read = asyncio.Event()
    real_issue_tokens = auth_service.issue_tokens
    real_revoke_all = auth_service.revoke_all_for_user

    async def issue_and_hold_uncommitted(own: AsyncSession, user: User) -> TokenPair:
        pair = await real_issue_tokens(own, user)
        await own.flush()
        rotation_pending.set()
        await wait_briefly(suspension_has_read)
        return pair

    async def revoke_all_while_rotation_pending(
        own: AsyncSession, user_id: uuid.UUID
    ) -> None:
        await wait_briefly(rotation_pending)
        await real_revoke_all(own, user_id)
        suspension_has_read.set()

    monkeypatch.setattr(auth_service, "issue_tokens", issue_and_hold_uncommitted)
    monkeypatch.setattr(
        auth_service, "revoke_all_for_user", revoke_all_while_rotation_pending
    )

    factory = get_session_factory()
    both_connected = asyncio.Barrier(2)

    async def refresh() -> None:
        async with factory() as own:
            await own.connection()
            await both_connected.wait()
            try:
                await auth_service.rotate_refresh_token(own, tokens.refresh_token)
            except InvalidToken:
                pass

    async def suspend() -> None:
        async with factory() as own:
            await own.connection()
            await both_connected.wait()
            await _suspend(own, registered_user.id)

    # The rotation may win or be refused, depending on who takes the user row
    # first; either way no session may survive.
    await asyncio.gather(refresh(), suspend())

    assert await live_token_count(session) == 0
