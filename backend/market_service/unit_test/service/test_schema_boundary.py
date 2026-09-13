"""The database boundary, asserted rather than assumed.

`sql/02-schemas.sql` deliberately grants nothing between schemas. That is the
only thing stopping this service from growing a convenient join into
`auth.users`, and a comment is not enforcement. These tests fail loudly if
somebody adds a grant to make something work.
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
    """The one that matters for [1.1] #1.

    market.markets.creator_id names a row in auth.users and cannot be a foreign
    key, because this query is a permission error. If it ever stops being one,
    the two services have been welded together and can no longer be separated.
    """
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM auth.users"))
    await session.rollback()


async def test_no_other_role_holds_any_privilege_on_this_schema(
    session: AsyncSession,
) -> None:
    """[1.1] #1's "not visible to traders", enforced one layer below the API.

    The route guard is what stops a trader reading a draft over HTTP. This is
    what stops any other service reading one straight out of the database, and
    it is the reason a draft is genuinely private rather than merely
    unexposed. A grant added here to make something convenient turns this red.
    """
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
