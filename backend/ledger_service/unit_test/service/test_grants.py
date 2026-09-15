"""The starting mock-credit grant. [B-1] #32, [A-1] #29"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from model.entities import AccountKind, Entry, Transaction, TransactionKind
from service import accounts, grants, ledger_service

ZERO = Decimal(0)


async def test_a_new_user_is_granted_on_first_read(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """"Each new account receives the configured fixed starting-credit amount."

    Nothing told this service the account was created. The auth service does
    not know that credits exist and there is no event between them, so the
    first read is what mints the grant.
    """
    balance = await ledger_service.balance_of_user(session, user_id)

    assert balance.amount == starting_credits


async def test_the_grant_is_an_append_only_ledger_entry(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """[B-1] #32's second criterion. Not a column, not a top-up: a real
    transaction, with both sides, that every later balance is derived from."""
    await grants.ensure_granted(session, user_id)

    account = await accounts.ensure(session, AccountKind.USER, user_id)
    entries = list(
        (
            await session.execute(select(Entry).where(Entry.account_id == account.id))
        ).scalars()
    )

    assert len(entries) == 1
    assert entries[0].amount == starting_credits
    assert entries[0].transaction.kind is TransactionKind.SIGNUP_GRANT


async def test_the_grant_moves_credits_rather_than_inventing_them(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """The platform is debited by exactly what the user is credited, which is
    what keeps the whole ledger at zero."""
    await grants.ensure_granted(session, user_id)

    platform = await accounts.ensure_platform(session)
    assert await accounts.balance_of(session, platform.id) == -starting_credits

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO


async def test_reading_twice_does_not_grant_twice(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """"Exactly once", and the reason a read is allowed to perform a write."""
    await ledger_service.balance_of_user(session, user_id)
    await ledger_service.balance_of_user(session, user_id)
    second = await ledger_service.balance_of_user(session, user_id)

    assert second.amount == starting_credits


async def test_the_key_is_the_user_id(user_id: uuid.UUID) -> None:
    """"Retrying account creation cannot produce multiple starting-credit
    grants" is true because there is nothing else the key could vary on."""
    assert grants.grant_key(user_id) == f"signup-grant:{user_id}"
    assert grants.grant_key(user_id) != grants.grant_key(uuid.uuid4())


async def test_concurrent_first_reads_grant_once(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """Two tabs, or a double-clicked login. Both find no entries, both mint,
    and the unique index on the key is what makes the loser a replay."""

    async def read_balance() -> Decimal:
        async with get_session_factory()() as s:
            return (await ledger_service.balance_of_user(s, user_id)).amount

    balances = await asyncio.gather(*(read_balance() for _ in range(6)))

    assert set(balances) == {starting_credits}

    granted = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.idempotency_key == grants.grant_key(user_id))
        )
    ).scalar_one()
    assert granted == 1


async def test_two_users_are_granted_separately(
    session: AsyncSession,
    user_id: uuid.UUID,
    other_user_id: uuid.UUID,
    starting_credits: Decimal,
) -> None:
    first = await ledger_service.balance_of_user(session, user_id)
    second = await ledger_service.balance_of_user(session, other_user_id)

    assert first.account_id != second.account_id
    assert first.amount == second.amount == starting_credits


async def test_reading_history_also_grants(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Either read closes the window, so a client that renders a statement
    before a balance sees the grant rather than an empty account."""
    page = await ledger_service.history_for_user(session, user_id, limit=10)

    assert len(page.entries) == 1
    assert page.entries[0].transaction.kind is TransactionKind.SIGNUP_GRANT
