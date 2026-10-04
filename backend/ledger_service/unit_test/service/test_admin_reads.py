"""An administrator's reads of somebody else's money mint nothing. #188.

The ledger holds no user table, so it cannot tell a user's id from a market's,
the platform's or a typo. A grant is permanent, so the admin reads open no
account and write no transaction; the user's own first read still mints.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MalformedCursor
from model.entities import PLATFORM_OWNER_ID, Account, AccountKind, Transaction
from service import accounts, grants, ledger_service


async def _rows_in(session: AsyncSession, table: type) -> int:
    return (await session.execute(select(func.count()).select_from(table))).scalar_one()


async def _id_named(session: AsyncSession, which: str) -> uuid.UUID:
    """An id that is not a user's, as an administrator might paste one."""
    if which == "platform owner":
        # The platform account exists once anybody has been granted.
        await grants.ensure_granted(session, uuid.uuid4())
        return PLATFORM_OWNER_ID
    if which == "market pool owner":
        market_id = uuid.uuid4()
        await accounts.ensure(session, AccountKind.MARKET_POOL, market_id)
        await session.commit()
        return market_id
    if which == "typo":
        return uuid.uuid4()
    raise ValueError(f"no id named {which!r}")


@pytest.mark.parametrize("which", ["platform owner", "market pool owner", "typo"])
async def test_an_admin_read_of_an_id_that_is_not_a_user_mints_nothing(
    session: AsyncSession, which: str
) -> None:
    """#188's first two criteria, and the typo the chosen option also covers."""
    target = await _id_named(session, which)
    transactions_before = await _rows_in(session, Transaction)
    accounts_before = await _rows_in(session, Account)

    balance = await ledger_service.balance_for_admin(session, target)
    page = await ledger_service.history_for_admin(session, target, limit=10)

    assert balance.amount == 0
    assert balance.account_id is None
    assert page.rows == []
    assert not page.has_more
    assert await _rows_in(session, Transaction) == transactions_before
    assert await _rows_in(session, Account) == accounts_before


async def test_the_user_s_own_first_read_still_mints_after_an_admin_looked(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """The admin sees zero before, and the user's starting credits after."""
    assert (await ledger_service.balance_for_admin(session, user_id)).amount == 0

    own = await ledger_service.balance_of_user(session, user_id)
    admin = await ledger_service.balance_for_admin(session, user_id)

    assert own.amount == starting_credits
    assert admin == own


async def test_a_bad_cursor_is_refused_even_for_an_id_with_no_account(
    session: AsyncSession,
) -> None:
    """Refused as on the minting route, not answered with an empty 200."""
    with pytest.raises(MalformedCursor):
        await ledger_service.history_for_admin(
            session, uuid.uuid4(), limit=10, cursor="nonsense"
        )
