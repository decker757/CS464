"""Accounts, and what they hold. [F-1] #41, ADR 0009.

An account is identity plus a row to lock. It holds no balance: `balance_of`
derives it from the account's entries on every read.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import PLATFORM_OWNER_ID, Account, AccountKind, Entry

ZERO = Decimal(0)


async def find(
    session: AsyncSession, kind: AccountKind, owner_id: uuid.UUID
) -> Account | None:
    stmt = select(Account).where(Account.kind == kind, Account.owner_id == owner_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def ensure(
    session: AsyncSession, kind: AccountKind, owner_id: uuid.UUID
) -> Account:
    """The account for this owner, created if it does not exist yet.

    Made on demand because this service is never told a user registered
    (ADR 0009). A racing insert loses on the (kind, owner_id) unique constraint
    and re-reads the winner's row. A SAVEPOINT, not a rollback: callers ensure
    several accounts before writing, and a rollback would discard the others.
    """
    existing = await find(session, kind, owner_id)
    if existing is not None:
        return existing

    account = Account(kind=kind, owner_id=owner_id)
    try:
        async with session.begin_nested():
            session.add(account)
            await session.flush()
    except IntegrityError:
        found = await find(session, kind, owner_id)
        if found is None:
            # Some other constraint fired, so this is not that race. Let it
            # out rather than loop.
            raise
        return found

    return account


async def ensure_platform(session: AsyncSession) -> Account:
    """The house account: exactly one, at a fixed owner id. Its balance is
    minus the credits in circulation, which is how credits are minted (ADR 0009).
    """
    return await ensure(session, AccountKind.PLATFORM, PLATFORM_OWNER_ID)


async def lock(session: AsyncSession, account_ids: list[uuid.UUID]) -> None:
    """Row-lock each account, one statement per account, ascending by id.

    What stops two concurrent debits both passing the overdraft check. Not one
    `IN (...) ORDER BY id FOR UPDATE`, which can still deadlock (ADR 0009).
    Entries are never locked: nothing updates one.
    """
    for account_id in sorted(account_ids):
        await session.execute(
            select(Account.id).where(Account.id == account_id).with_for_update()
        )


def balance_query_of(
    *, account_id: uuid.UUID | ColumnElement[uuid.UUID]
) -> Select[tuple[Decimal]]:
    """The sum of one account's entries, zero when it has none: a single-column
    SELECT, so `.scalar_subquery()` turns it into a value inside another
    statement. A balance is never stored (ADR 0009); this is how it is read.

    `account_id` is an id, or a column of the enclosing statement (for example
    `MarketBook.pool_account_id`) that the subquery correlates on.
    """
    return select(func.coalesce(func.sum(Entry.amount), ZERO)).where(
        Entry.account_id == account_id
    )


async def balance_of(session: AsyncSession, account_id: uuid.UUID) -> Decimal:
    """What this account holds: the sum of its entries, derived on every read,
    never stored (ADR 0009). [B-1] #32, [B-2] #33.

    Zero for an account with no entries. [4.1] #13's running balance is not
    read here: `ledger_service._page_query` sums to a position inside the
    page's own statement.
    """
    stmt = balance_query_of(account_id=account_id)
    return (await session.execute(stmt)).scalar_one()
