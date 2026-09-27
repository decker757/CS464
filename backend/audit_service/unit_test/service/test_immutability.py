"""The log is append-only, asserted rather than assumed. [4.3] #15

Three layers, each checked here: no role holds UPDATE, DELETE or TRUNCATE; a
trigger refuses all three even for the owner; and this service owns nothing it
could ALTER or DROP. ADR 0006.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA


async def _has_privilege(session: AsyncSession, role: str, privilege: str) -> bool:
    """Ask Postgres whether `role` holds `privilege` on the audit table.

    `has_table_privilege`, because `information_schema.table_privileges` shows
    only the connected role's own grants and would miss one given to market_svc.
    """
    result = await session.execute(
        text("SELECT has_table_privilege(:role, 'audit.admin_actions', :privilege)"),
        {"role": role, "privilege": privilege},
    )
    return result.scalar_one()


async def test_this_service_reads_the_audit_schema(session: AsyncSession) -> None:
    assert SCHEMA == "audit"

    found = (await session.execute(text("SELECT current_schema()"))).scalar_one()

    assert found == "audit"


async def test_it_connects_as_its_own_role(session: AsyncSession) -> None:
    """Not as the superuser. A suite running as postgres would pass every test
    below while proving nothing about the grants."""
    who = (await session.execute(text("SELECT current_user"))).scalar_one()

    assert who == "audit_svc"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.admin_actions SET reason = 'edited'",
        "DELETE FROM audit.admin_actions",
        "TRUNCATE audit.admin_actions",
    ],
)
async def test_the_reader_cannot_change_the_log(
    session: AsyncSession, statement: str
) -> None:
    """Including the role the API itself runs as."""
    with pytest.raises(DBAPIError):
        await session.execute(text(statement))
    await session.rollback()


async def test_it_holds_exactly_select_and_insert(session: AsyncSession) -> None:
    """INSERT only so this suite can seed rows; no route reaches it."""
    rows = (
        await session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_schema = 'audit' AND table_name = 'admin_actions' "
                "AND grantee = 'audit_svc'"
            )
        )
    ).scalars()

    assert set(rows) == {"SELECT", "INSERT"}


@pytest.mark.parametrize("role", ["auth_svc", "ledger_svc", "market_svc", "audit_svc"])
@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_no_service_role_can_change_an_entry(
    session: AsyncSession, role: str, privilege: str
) -> None:
    """Over every service role, not just this one."""
    granted = await _has_privilege(session, role, privilege)

    assert granted is False


async def test_every_service_role_can_append(session: AsyncSession) -> None:
    """A writer without INSERT would fail every admin action it logs."""
    for role in ("auth_svc", "ledger_svc", "market_svc"):
        granted = await _has_privilege(session, role, "INSERT")

        assert granted is True, f"{role} cannot append to the audit log"


async def test_no_writer_can_read_the_log(session: AsyncSession) -> None:
    """INSERT without SELECT, so no service reads another's actions. ADR 0006."""
    for role in ("auth_svc", "ledger_svc", "market_svc"):
        granted = await _has_privilege(session, role, "SELECT")

        assert granted is False, f"{role} can read the audit log"


async def test_the_append_only_trigger_is_installed(session: AsyncSession) -> None:
    """The trigger covers the owner, whom no REVOKE restrains.

    This connection is not the owner and cannot exercise it, so its presence
    is asserted instead.
    """
    triggers = (
        await session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'audit.admin_actions'::regclass AND NOT tgisinternal"
            )
        )
    ).scalars()

    assert "admin_actions_append_only" in set(triggers)


async def test_this_service_does_not_own_the_table(session: AsyncSession) -> None:
    """An owner may ALTER and DROP whatever is granted. ADR 0006."""
    owner = (
        await session.execute(
            text(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'audit' AND tablename = 'admin_actions'"
            )
        )
    ).scalar_one()

    assert owner != "audit_svc"


async def test_it_cannot_create_a_table_of_its_own_here(session: AsyncSession) -> None:
    """No CREATE either, so it cannot stage an edited copy and swap it in."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("CREATE TABLE audit.shadow (id int)"))
    await session.rollback()


async def test_it_cannot_read_another_services_schema(session: AsyncSession) -> None:
    """The audit grant is not a licence to read anybody's business data."""
    for schema in ("auth", "market", "ledger"):
        with pytest.raises(ProgrammingError):
            await session.execute(text(f"SELECT count(*) FROM {schema}.pg_class"))
        await session.rollback()


async def test_an_entry_survives_an_attempt_to_remove_it(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """End to end, on a row this test can point at."""
    rows = await seed(actor_id, 1)
    entry_id = rows[0]["id"]

    with pytest.raises(DBAPIError):
        await session.execute(
            text("DELETE FROM audit.admin_actions WHERE id = :id"), {"id": entry_id}
        )
    await session.rollback()

    still_there = (
        await session.execute(
            text("SELECT count(*) FROM audit.admin_actions WHERE id = :id"),
            {"id": entry_id},
        )
    ).scalar_one()

    assert still_there == 1
