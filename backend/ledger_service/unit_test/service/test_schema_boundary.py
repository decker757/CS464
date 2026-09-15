"""The database boundary, asserted rather than assumed.

`sql/02-schemas.sql` deliberately grants nothing between schemas. That is the
only thing stopping this service from growing a convenient join into
`auth.users` or `market.markets`, and a comment is not enforcement. These tests
fail loudly if somebody adds a grant to make something work.

This service is the one where that matters most. It is the only one that holds
money, and it is the one every other service has the most reason to want to
read directly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA


async def test_this_service_owns_the_ledger_schema(session: AsyncSession) -> None:
    assert SCHEMA == "ledger"

    found = (await session.execute(text("SELECT current_schema()"))).scalar_one()

    assert found == "ledger"


async def test_it_connects_as_its_own_role(session: AsyncSession) -> None:
    """Not as the superuser. A suite running as postgres would pass while
    proving nothing about the grants — and here it would also pass the
    append-only tests for the wrong reason."""
    who = (await session.execute(text("SELECT current_user"))).scalar_one()

    assert who == "ledger_svc"


async def test_it_cannot_read_the_auth_schema(session: AsyncSession) -> None:
    """`ledger.accounts.owner_id` names a row in `auth.users` and cannot be a
    foreign key, because this query is a permission error. If it ever stops
    being one, the two services have been welded together."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM auth.users"))
    await session.rollback()


async def test_it_cannot_read_the_market_schema(session: AsyncSession) -> None:
    """ADR 0005 has `b` and the seed subsidy crossing at publish, as a
    snapshot. A snapshot is a copy that arrives over the wire; this is what
    stops it quietly becoming a join instead."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM market.markets"))
    await session.rollback()


async def test_no_other_role_holds_any_privilege_on_this_schema(
    session: AsyncSession,
) -> None:
    """Nobody else reads the ledger, including the services that will call it.

    A trading service composing a quote does it over HTTP, against the balance
    this service derives. A grant added here to make that cheaper would make
    two services responsible for one invariant.
    """
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT grantee FROM information_schema.table_privileges "
                "WHERE table_schema = 'ledger'"
            )
        )
    ).scalars()

    assert set(rows) == {"ledger_svc"}


async def test_it_cannot_create_tables_in_public(session: AsyncSession) -> None:
    """A table in public would belong to nobody and be visible to everybody."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("CREATE TABLE public.sneaky (id int)"))
    await session.rollback()


async def test_the_audit_grant_is_exactly_insert(session: AsyncSession) -> None:
    """The one deliberate exception, and its exact shape. ADR 0006.

    This service holds INSERT on `audit.admin_actions` and nothing else, so it
    can record an admin action in the same transaction as the action — [3.4]
    #12's settlement is the one that will — without being able to read what any
    other service recorded. The same assertion guards the market service, and
    widening either turns both red.
    """
    privileges = (
        await session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_schema = 'audit' AND table_name = 'admin_actions' "
                "AND grantee = 'ledger_svc'"
            )
        )
    ).scalars()

    assert set(privileges) == {"INSERT"}


async def test_it_cannot_read_the_audit_log(session: AsyncSession) -> None:
    """The consequence of the grant above, stated as behaviour."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM audit.admin_actions"))
    await session.rollback()
