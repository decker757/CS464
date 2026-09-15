"""Accounts, and what they hold. [F-1] #41

An account is identity plus a row to lock. It holds no balance: the balance is
derived here, by summing the account's entries, every time anybody asks.

HTTP is not mentioned in this file, and neither is the difference between a
grant and a trade. It knows about accounts and about arithmetic.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import PLATFORM_OWNER_ID, Account, AccountKind, Entry

ZERO = Decimal(0)


async def ensure(
    session: AsyncSession, kind: AccountKind, owner_id: uuid.UUID
) -> Account:
    """The account for this owner, created if it does not exist yet.

    Accounts are made on demand rather than at registration, because this
    service is never told that a registration happened — the auth service does
    not know that credits exist and there is no event between them. See
    `service/grants.py`; this is the same lazy argument one layer down.

    The find-then-insert race is real and is handled rather than narrowed: two
    tabs loading a balance at once both find nothing and both insert. The
    unique constraint on (kind, owner_id) makes the loser an IntegrityError,
    and the row it wanted is by then the row the winner committed, so the
    retry simply finds it. Same shape, and the same reasoning, as the retry in
    `market_service.save`.

    A SAVEPOINT rather than that retry's outright rollback, and the difference
    matters here. Callers ensure several accounts before writing anything —
    `service/grants.py` ensures two — so a plain rollback on the second would
    silently discard the first, and the caller would carry on holding an object
    whose INSERT no longer exists. `begin_nested` undoes only the insert that
    failed.
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
            # The constraint that fired was not the one we expected, so this is
            # not the race described above. Let it out rather than looping.
            raise
        return found

    return account


async def find(
    session: AsyncSession, kind: AccountKind, owner_id: uuid.UUID
) -> Account | None:
    stmt = select(Account).where(Account.kind == kind, Account.owner_id == owner_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def ensure_platform(session: AsyncSession) -> Account:
    """The house account. Exactly one, at a fixed owner id.

    Its balance is minus the credits in circulation, and it is the only account
    allowed below zero. That is what makes the starting grant a movement
    between two accounts rather than credits appearing from nowhere, and it is
    why `SUM(amount)` over the whole of `ledger.entries` is always exactly zero.
    """
    return await ensure(session, AccountKind.PLATFORM, PLATFORM_OWNER_ID)


async def lock(session: AsyncSession, account_ids: list[uuid.UUID]) -> None:
    """Take a row lock on each account, in a fixed order.

    This is the "serialization so concurrent trades cannot overdraw" half of
    [F-1] #41. Two buys from one trader both read a balance, both decide there
    is enough, and both write — unless the second one waits. Under READ
    COMMITTED the loser blocks here and, when it proceeds, sees the entries the
    winner committed, so the balance it checks against is the real one.

    One statement per account, ascending by id, rather than a single
    `WHERE id IN (...) ORDER BY id FOR UPDATE`. Postgres locks rows in the
    order the plan produces them, which an ORDER BY in the same statement does
    not reliably constrain, so the single-statement version can still have two
    transactions take the same pair of locks in opposite orders and deadlock.
    Ascending id is an order every caller agrees on without coordinating.

    The lock covers the account rows alone. Entries are never locked and never
    need to be: nothing updates one, so there is no row whose value can change
    under a reader.
    """
    for account_id in sorted(account_ids):
        await session.execute(
            select(Account.id).where(Account.id == account_id).with_for_update()
        )


async def balance_of(session: AsyncSession, account_id: uuid.UUID) -> Decimal:
    """What this account holds, derived. [B-1] #32, [B-2] #33, [4.1] #13.

    The sum of its entries and nothing else. There is no cached column to fall
    out of step with this, which is the whole design: "the displayed balance
    equals the sum of the user's ledger entries" is not a reconciliation this
    service has to perform, it is the only way this service can answer the
    question.

    An account with no entries is zero rather than an error, so a caller does
    not have to know whether a row exists before asking what is in it.
    """
    stmt = select(func.coalesce(func.sum(Entry.amount), ZERO)).where(
        Entry.account_id == account_id
    )
    return (await session.execute(stmt)).scalar_one()
