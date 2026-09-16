"""What reaches the audit log from this service, and what does not. [4.3] #15

Every test here reads the log back as `audit_svc`, because `auth_svc` cannot —
it holds INSERT and no SELECT, which is what stops one service reading
another's actions.

The log is append-only and nothing may truncate it, so rows from earlier tests
in this database are still present. Each test scopes itself to an actor id no
other test has used.
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
    """[4.4] #16's third criterion: role changes are logged."""
    actor = _actor()

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


async def test_it_records_both_ends_of_the_change(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """Which role, from which. A log that says only "role changed" answers nothing."""
    actor = _actor()

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.ADMIN
    )

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["context"] == {"from": "trader", "to": "admin"}


async def test_a_demotion_is_recorded_too(
    session: AsyncSession,
    audit_reader: AsyncSession,
    registered_user: User,
    another_administrator: User,
) -> None:
    registered_user.role = UserRole.ADMIN
    await session.commit()
    actor = _actor()

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.TRADER
    )

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["context"] == {"from": "admin", "to": "trader"}


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


async def test_an_absent_reason_is_null_rather_than_empty(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """The reason is optional; the entry is not."""
    actor = _actor()

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.ADMIN
    )

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1
    assert entries[0]["reason"] is None


async def test_two_concurrent_promotions_of_one_trader_are_recorded_once(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """Two administrators promote the same person in the same instant.

    Two real transactions. Read without a lock both see TRADER, both write
    ADMIN, and both append a `trader → admin`: one transition attributed to two
    people, in a log whose purpose is to say who did what. `change_role` locks
    the target before reading the role, so the second request waits for the
    first, re-reads ADMIN, and is the idempotent case — no write and no entry.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    first, second = _actor(username="ernest_t"), _actor(username="ihsan_b")
    factory = get_session_factory()

    # Both connected before either starts, so the outcome is decided by the
    # lock and not by which session had to open a connection first.
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


async def test_it_records_who_the_actor_was_rather_than_who_they_are(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """A snapshot, so a later rename or demotion cannot rewrite history.

    `audit_svc` holds no grant on auth.users, so an entry that named the actor
    only by id would be unreadable to the one role allowed to read it.
    """
    actor = _actor(username="ernest_t")

    await user_admin.change_role(
        session, actor=actor, target_id=registered_user.id, role=UserRole.ADMIN
    )

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["actor_id"] == actor.id
    assert entry["actor_username"] == "ernest_t"
    assert entry["actor_role"] == "admin"


# --- what does not get recorded -------------------------------------------
async def test_a_change_to_the_role_already_held_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession, registered_user: User
) -> None:
    """A request that changed nothing is not a decision.

    The same argument ADR 0006 makes about draft autosave: an audit log that
    collects non-events is a log nobody finishes reading.
    """
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
    """The reason every test above needs a second connection.

    INSERT and no SELECT is what stops the auth service reading what the market
    service did. Granting SELECT here to make a test simpler would be a
    decision to couple them; ADR 0006 argues the shape of the exception.
    """
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT 1 FROM audit.admin_actions"))


async def test_the_audit_grant_is_exactly_insert(session: AsyncSession) -> None:
    """The market service asserts the same shape for market_svc.

    SELECT here would let this service read what market and the ledger did.
    UPDATE or DELETE would end the append-only guarantee. Either turns this red.
    """
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
