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

from core.errors import CannotChangeOwnRole, UserNotFound
from core.roles import UserRole
from model.audit import AdminAction
from model.entities import User
from service import user_admin
from service.audit import Actor

_ENTRIES = text(
    "SELECT * FROM audit.admin_actions WHERE actor_id = :actor "
    "ORDER BY occurred_at DESC, id DESC"
)


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
