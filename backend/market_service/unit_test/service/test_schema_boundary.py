"""The database boundary, asserted rather than assumed. Guards, not decoration.

`sql/02-schemas.sql` grants nothing between schemas; these fail if somebody
adds a grant to make something convenient. ADR 0003, ADR 0006.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA


async def test_this_service_owns_the_market_schema(session: AsyncSession) -> None:
    assert SCHEMA == "market"

    found = (
        await session.execute(
            text("SELECT current_schema()"),
        )
    ).scalar_one()

    assert found == "market"


async def test_it_connects_as_its_own_role(session: AsyncSession) -> None:
    """Not as the superuser. A suite running as postgres would pass while
    proving nothing about the grants."""
    who = (await session.execute(text("SELECT current_user"))).scalar_one()

    assert who == "market_svc"


async def test_it_cannot_read_the_auth_schema(session: AsyncSession) -> None:
    """If this ever stops being a permission error, the services are welded together."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM auth.users"))
    await session.rollback()


async def test_no_other_role_holds_any_privilege_on_this_schema(
    session: AsyncSession,
) -> None:
    """[1.1] #1's draft privacy, one layer below the API: no other service may read it."""
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT grantee FROM information_schema.table_privileges "
                "WHERE table_schema = 'market'"
            )
        )
    ).scalars()

    assert set(rows) == {"market_svc"}


async def test_it_cannot_create_tables_in_public(session: AsyncSession) -> None:
    """A table in public would belong to nobody and be visible to everybody."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("CREATE TABLE public.sneaky (id int)"))
    await session.rollback()


async def test_the_audit_grant_is_exactly_insert(session: AsyncSession) -> None:
    """[4.3] #15's one deliberate exception, by shape: INSERT and nothing else.

    SELECT would read other services' actions; UPDATE or DELETE would end the
    append-only log. ADR 0006.
    """
    rows = (
        await session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_schema = 'audit' AND table_name = 'admin_actions' "
                "AND grantee = 'market_svc'"
            )
        )
    ).scalars()

    assert set(rows) == {"INSERT"}
