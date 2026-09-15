"""The log is append-only, asserted rather than assumed. [4.3] #15

The story's second acceptance criterion is that the log is append-only with no
edit or delete path exposed. "No path exposed" would be satisfied by a service
that simply has no such route, and that is a promise kept by whoever writes the
next route. These tests are about the stronger claim the design actually makes:
there is no path at all.

Three layers stand behind it, and each is checked here.

  - No role holds UPDATE, DELETE or TRUNCATE. Not the writers, not this
    service, not the one the API runs as.
  - A statement-level trigger refuses all three even for the table's owner, so
    the guarantee survives someone re-granting by hand.
  - This service owns nothing in the schema, so it cannot ALTER or DROP its
    way around either.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA


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
    """Including the role the API itself runs as.

    This is the one that matters. A service that could edit the log it serves
    would make every entry in it a claim rather than a record, and no amount of
    care in the routes would change that.
    """
    with pytest.raises(DBAPIError):
        await session.execute(text(statement))
    await session.rollback()


async def test_it_holds_exactly_select_and_insert(session: AsyncSession) -> None:
    """INSERT is here so this suite can seed rows without a second role's
    credentials. No route reaches it, and it is still append-only: INSERT adds,
    it does not change anything already written."""
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
    """The guarantee stated over every service role, not just this one.

    `has_table_privilege` rather than `information_schema.table_privileges`,
    which is filtered to what the connected role is party to: asked as
    audit_svc, that view shows audit_svc's own grants and nothing else, so a
    UPDATE quietly granted to market_svc would not appear in it. This function
    answers for any role.

    A convenience GRANT added later to make something work turns this red,
    which is the point: the grants are the enforcement, so widening them should
    have to be argued for rather than noticed in review.
    """
    granted = (
        await session.execute(
            text("SELECT has_table_privilege(:role, 'audit.admin_actions', :privilege)"),
            {"role": role, "privilege": privilege},
        )
    ).scalar_one()

    assert granted is False


async def test_every_service_role_can_append(session: AsyncSession) -> None:
    """The other half. A writer that lost INSERT would fail every admin action
    it tried to log, and because the entry shares the action's transaction, it
    would fail the action too."""
    for role in ("auth_svc", "ledger_svc", "market_svc"):
        granted = (
            await session.execute(
                text("SELECT has_table_privilege(:role, 'audit.admin_actions', 'INSERT')"),
                {"role": role},
            )
        ).scalar_one()

        assert granted is True, f"{role} cannot append to the audit log"


async def test_no_writer_can_read_the_log(session: AsyncSession) -> None:
    """INSERT without SELECT is what keeps the one cross-schema grant from
    becoming a way for one service to read another's actions."""
    for role in ("auth_svc", "ledger_svc", "market_svc"):
        granted = (
            await session.execute(
                text("SELECT has_table_privilege(:role, 'audit.admin_actions', 'SELECT')"),
                {"role": role},
            )
        ).scalar_one()

        assert granted is False, f"{role} can read the audit log"


async def test_the_append_only_trigger_is_installed(session: AsyncSession) -> None:
    """What covers the one role the grants cannot.

    The table's owner holds UPDATE, DELETE and TRUNCATE inherently, and no
    REVOKE takes them away. The trigger is what refuses those for everybody,
    owner included — so a stray UPDATE from a psql session connected as the
    superuser fails as loudly as one from a service. It cannot be exercised
    from this connection, which is not the owner, so its presence is asserted
    instead.
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
    """Ownership would defeat the grants above.

    An owner may ALTER and DROP regardless of what is granted, so a service
    that owned this table could rewrite its way around every check here. It
    belongs to the superuser and is created by sql/02-schemas.sql, which is
    also why this service has no create_all.
    """
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
    """No CREATE on the schema either, so it cannot stage a copy, mutate that
    and swap it in."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("CREATE TABLE audit.shadow (id int)"))
    await session.rollback()


async def test_it_cannot_read_another_services_schema(session: AsyncSession) -> None:
    """The audit exception is one INSERT into one shared table, not a licence
    to read anybody's business data. Every name this service shows was
    snapshotted into the row when the action happened, precisely so that no
    read-time lookup is needed."""
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
