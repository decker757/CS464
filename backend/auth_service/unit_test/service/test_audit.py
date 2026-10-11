"""What reaches the audit log from this service, and what does not. [4.3] #15

Read back as `audit_svc`, since `auth_svc` holds INSERT only. Each test scopes
itself to a fresh actor id, since the log cannot be emptied. ADR 0006.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import CannotChangeOwnRole, CannotSuspendSelf, UserNotFound
from core.roles import UserRole
from model.audit import AdminAction
from model.entities import User
from service import audit, user_admin
from service.audit import Actor
from unit_test.service.user_rows import admin_row

_ENTRIES = text(
    "SELECT * FROM audit.admin_actions WHERE actor_id = :actor "
    "ORDER BY occurred_at DESC, id DESC"
)


_SUSPENSION_REASON = "  Second account of bob_1, same device and same card.  "


def _actor(user_id: uuid.UUID | None = None, username: str = "ernest_t") -> Actor:
    return Actor(id=user_id or uuid.uuid4(), username=username, role="admin")


async def _entries(audit_reader: AsyncSession, actor: Actor) -> list:
    return list((await audit_reader.execute(_ENTRIES, {"actor": actor.id})).mappings())


# --- what gets recorded ---------------------------------------------------
async def test_a_role_change_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """[4.4] #16's third criterion: who changed whose role, from what to what.

    The actor is a snapshot, so a later rename or demotion cannot rewrite it.
    Named apart from the target (`ernest_t`), so the two cannot be confused.
    """
    actor = _actor(username="ihsan_b")

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.ADMIN
    )

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1

    entry = entries[0]
    assert entry["action_type"] == AdminAction.USER_ROLE_CHANGED.value
    assert entry["target_type"] == "user"
    assert entry["target_id"] == registered_user.id
    assert entry["target_label"] == registered_user.username
    assert entry["source_service"] == "auth_service"
    assert entry["context"] == {"from": "trader", "to": "admin"}
    assert entry["reason"] is None
    assert entry["actor_id"] == actor.id
    assert entry["actor_username"] == "ihsan_b"
    assert entry["actor_role"] == "admin"


async def test_the_reason_is_recorded_verbatim(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    actor = _actor()
    reason = "Covering market resolution while Ihsan is away."

    await user_admin.change_role(
        session,
        actor=actor,
        target_id=registered_user.id,
        role=UserRole.ADMIN,
        reason=reason,
    )

    assert (await _entries(audit_reader, actor))[0]["reason"] == reason


async def test_two_concurrent_promotions_of_one_trader_are_recorded_once(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """Two administrators promote the same person at once. ADR 0015.

    The second waits on the target's lock, re-reads ADMIN, and writes nothing.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    first, second = _actor(username="ernest_t"), _actor(username="ihsan_b")
    factory = get_session_factory()

    # Both connected first, so the lock decides the outcome. ADR 0015.
    barrier = asyncio.Barrier(2)

    async def promote(actor: Actor) -> None:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            await user_admin.change_role(
                own, actor=actor, target_id=registered_user.id, role=UserRole.ADMIN
            )

    await asyncio.gather(promote(first), promote(second))

    entries = await _entries(audit_reader, first) + await _entries(audit_reader, second)
    assert len(entries) == 1
    assert entries[0]["context"] == {"from": "trader", "to": "admin"}


@pytest.mark.parametrize("role", [UserRole.TRADER, UserRole.ADMIN])
@pytest.mark.parametrize(
    ("suspended", "action"),
    [(True, AdminAction.USER_SUSPENDED), (False, AdminAction.USER_UNSUSPENDED)],
)
async def test_a_suspension_is_recorded_with_its_trimmed_reason_and_the_role(
    session: AsyncSession,
    audit_reader: AsyncSession,
    registered_user: User,
    another_administrator: User,
    suspended: bool,
    action: AdminAction,
    role: UserRole,
) -> None:
    """[4.2] #14: requires a reason, and the log is the only place it lives.

    `context.role` tells an administrator's suspension from a trader's after a
    later role change. DECISIONS.md, "The suspension's audit entry carries the
    target's role in `context`". The other administrator is there so the
    last-administrator guard lets an admin target be suspended.
    """
    actor = _actor(username="ihsan_b")
    registered_user.role = role
    registered_user.is_suspended = not suspended
    await session.commit()

    await user_admin.set_suspension(
        session,
        actor=actor,
        target_id=registered_user.id,
        suspended=suspended,
        reason=_SUSPENSION_REASON,
    )

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1

    entry = entries[0]
    assert entry["action_type"] == action.value
    assert entry["target_type"] == "user"
    assert entry["target_id"] == registered_user.id
    assert entry["target_label"] == registered_user.username
    assert entry["reason"] == _SUSPENSION_REASON.strip()
    assert entry["context"] == {"role": role.value}
    assert entry["actor_id"] == actor.id
    assert entry["actor_username"] == "ihsan_b"


async def test_two_concurrent_suspensions_of_one_user_are_recorded_once(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """Two administrators suspend the same account at once. ADR 0015.

    The second waits on the target's lock, re-reads it suspended, and writes
    nothing. DECISIONS.md, "A suspension that changes nothing writes nothing,
    decided from the locked row".
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    first, second = _actor(username="ernest_t"), _actor(username="ihsan_b")
    factory = get_session_factory()

    # Both connected first, so the lock decides the outcome. ADR 0015.
    barrier = asyncio.Barrier(2)

    async def suspend(actor: Actor) -> None:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            await user_admin.set_suspension(
                own,
                actor=actor,
                target_id=registered_user.id,
                suspended=True,
                reason=_SUSPENSION_REASON,
            )

    await asyncio.gather(suspend(first), suspend(second))

    entries = await _entries(audit_reader, first) + await _entries(audit_reader, second)
    assert len(entries) == 1


# --- what does not get recorded -------------------------------------------
async def test_a_change_to_the_role_already_held_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """A request that changed nothing is not a decision."""
    actor = _actor()

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.TRADER
    )

    assert await _entries(audit_reader, actor) == []


async def test_a_refused_self_change_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    actor = _actor(registered_user.id)

    with pytest.raises(CannotChangeOwnRole):
        await user_admin.change_role(
            session, actor=actor, target_id=registered_user.id, role=UserRole.TRADER
        )

    assert await _entries(audit_reader, actor) == []


async def test_a_suspension_to_the_state_already_held_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """The role change's rule: a request that changed nothing is not a decision."""
    actor = _actor()

    await user_admin.set_suspension(
        session,
        actor=actor,
        target_id=registered_user.id,
        suspended=False,
        reason=_SUSPENSION_REASON,
    )

    assert await _entries(audit_reader, actor) == []


async def test_suspending_an_already_suspended_administrator_is_a_quiet_no_op(
    session: AsyncSession, audit_reader: AsyncSession, another_administrator: User
) -> None:
    """A suspended administrator does not count toward the last one, but is
    still a target: suspending them again is the no-op rule, not
    `LastAdministrator`, though only one active administrator is left. ADR 0007.
    """
    suspended_admin = admin_row("admin_ghost", suspended=True)
    session.add(suspended_admin)
    await session.commit()
    actor = _actor()

    user = await user_admin.set_suspension(
        session,
        actor=actor,
        target_id=suspended_admin.id,
        suspended=True,
        reason=_SUSPENSION_REASON,
    )

    assert user.is_suspended is True
    assert await _entries(audit_reader, actor) == []


async def test_a_refused_self_suspension_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    actor = _actor(registered_user.id)

    with pytest.raises(CannotSuspendSelf):
        await user_admin.set_suspension(
            session,
            actor=actor,
            target_id=registered_user.id,
            suspended=True,
            reason=_SUSPENSION_REASON,
        )

    assert await _entries(audit_reader, actor) == []


async def test_a_change_to_an_unknown_user_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    actor = _actor()

    with pytest.raises(UserNotFound):
        await user_admin.change_role(
            session, actor=actor, target_id=uuid.uuid4(), role=UserRole.ADMIN
        )

    assert await _entries(audit_reader, actor) == []


async def test_a_second_change_appends_rather_than_replaces(
    session: AsyncSession,
    audit_reader: AsyncSession,
    registered_user: User,
    another_administrator: User,
) -> None:
    """Append-only, from the writer's side. Two decisions, two rows."""
    actor = _actor()

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.ADMIN
    )
    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.TRADER
    )

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 2
    assert [e["context"] for e in entries] == [
        {"from": "admin", "to": "trader"},
        {"from": "trader", "to": "admin"},
    ]


async def test_an_entry_is_not_committed_by_its_writer(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0006: the entry exists only if the action's own commit says so."""
    actor = _actor()

    await audit.record(
        session, actor=actor, action=AdminAction.USER_ROLE_CHANGED, target_type="user"
    )
    await session.rollback()

    assert await _entries(audit_reader, actor) == []


# --- the grant this depends on --------------------------------------------
async def test_this_service_cannot_read_the_log_it_writes_to(
    session: AsyncSession,
) -> None:
    """Why the tests above need a second connection. Do not grant SELECT. ADR 0006."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT 1 FROM audit.admin_actions"))


async def test_the_audit_grant_is_exactly_insert(session: AsyncSession) -> None:
    """Guard: exactly INSERT, as the market service asserts for market_svc. ADR 0006."""
    rows = (
        await session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_schema = 'audit' AND table_name = 'admin_actions' "
                "AND grantee = 'auth_svc'"
            )
        )
    ).scalars()

    assert set(rows) == {"INSERT"}
