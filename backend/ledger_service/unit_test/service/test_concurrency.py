"""Concurrent writers. [F-1] #41's "serialization so concurrent trades cannot
overdraw", and [T-2] #22's fourth acceptance criterion in advance.

Every test here uses its own session per writer, because that is the only way
to have two real database transactions. A single session running two coroutines
shares one connection and one transaction, which would prove nothing: the thing
under test is what Postgres does when two transactions want the same row.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import InsufficientFunds
from model.entities import AccountKind, Entry, Transaction, TransactionKind
from service import accounts, posting
from service.posting import Leg

ZERO = Decimal(0)


async def _spend(user_id: uuid.UUID, amount: Decimal) -> str:
    """One withdrawal, in its own session and its own transaction.

    Returns what happened rather than raising, so a caller can gather all of
    them and count the outcomes.
    """
    async with get_session_factory()() as session:
        user = await accounts.ensure(session, AccountKind.USER, user_id)
        platform = await accounts.ensure_platform(session)
        try:
            await posting.post(
                session,
                idempotency_key=f"spend:{uuid.uuid4()}",
                kind=TransactionKind.SIGNUP_GRANT,
                legs=[
                    Leg(account=user, amount=-amount),
                    Leg(account=platform, amount=amount),
                ],
            )
        except InsufficientFunds:
            return "refused"
        return "accepted"


async def _grant(user_id: uuid.UUID, amount: Decimal) -> None:
    async with get_session_factory()() as session:
        user = await accounts.ensure(session, AccountKind.USER, user_id)
        platform = await accounts.ensure_platform(session)
        await posting.post(
            session,
            idempotency_key=f"fund:{uuid.uuid4()}",
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=platform, amount=-amount),
                Leg(account=user, amount=amount),
            ],
        )


async def test_concurrent_spends_cannot_overdraw(session: AsyncSession) -> None:
    """Ten simultaneous withdrawals of 100 against a balance of 500.

    Exactly five may succeed. Without the row lock in `accounts.lock`, all ten
    read the same balance, all ten decide there is enough, and the account ends
    at minus five hundred.
    """
    user_id = uuid.uuid4()
    await _grant(user_id, Decimal("500"))

    outcomes = await asyncio.gather(
        *(_spend(user_id, Decimal("100")) for _ in range(10))
    )

    assert outcomes.count("accepted") == 5
    assert outcomes.count("refused") == 5

    account = await accounts.ensure(session, AccountKind.USER, user_id)
    assert await accounts.balance_of(session, account.id) == ZERO


async def test_no_balance_ever_goes_negative(session: AsyncSession) -> None:
    """The property, rather than the arithmetic of one scenario."""
    user_id = uuid.uuid4()
    await _grant(user_id, Decimal("300"))

    await asyncio.gather(*(_spend(user_id, Decimal("70")) for _ in range(12)))

    account = await accounts.ensure(session, AccountKind.USER, user_id)
    assert await accounts.balance_of(session, account.id) >= ZERO


async def test_the_ledger_still_balances_afterwards(session: AsyncSession) -> None:
    """The single strongest assertion available about this service.

    Every entry ever written, summed. If a transaction ever committed half of
    itself, or a leg was dropped under contention, or an amount was rounded on
    one side and not the other, this is not zero.
    """
    user_id = uuid.uuid4()
    await _grant(user_id, Decimal("500"))

    await asyncio.gather(*(_spend(user_id, Decimal("100")) for _ in range(10)))

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()

    assert total == ZERO


async def test_every_transaction_balances_on_its_own(session: AsyncSession) -> None:
    """The whole ledger summing to zero would also be true if two unrelated
    mistakes cancelled out. This is the per-transaction version."""
    user_id = uuid.uuid4()
    await _grant(user_id, Decimal("500"))
    await asyncio.gather(*(_spend(user_id, Decimal("100")) for _ in range(10)))

    unbalanced = (
        await session.execute(
            select(Entry.transaction_id)
            .group_by(Entry.transaction_id)
            .having(func.sum(Entry.amount) != ZERO)
        )
    ).scalars()

    assert list(unbalanced) == []


async def _post_with_key(user_id: uuid.UUID, key: str, amount: Decimal) -> uuid.UUID:
    async with get_session_factory()() as session:
        user = await accounts.ensure(session, AccountKind.USER, user_id)
        platform = await accounts.ensure_platform(session)
        transaction = await posting.post(
            session,
            idempotency_key=key,
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=platform, amount=-amount),
                Leg(account=user, amount=amount),
            ],
        )
        return transaction.id


async def test_one_key_under_contention_writes_one_transaction(
    session: AsyncSession,
) -> None:
    """Eight callers, one key. The unique index catches the seven losers and
    `posting.post` turns each into a replay of the winner's transaction rather
    than an error — which is what makes it safe to retry a trade whose response
    was lost."""
    user_id = uuid.uuid4()
    key = f"race:{uuid.uuid4()}"

    ids = await asyncio.gather(
        *(_post_with_key(user_id, key, Decimal("100")) for _ in range(8))
    )

    assert len(set(ids)) == 1

    written = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.idempotency_key == key
            )
        )
    ).scalar_one()
    assert written == 1


async def test_concurrent_first_reads_create_one_account(
    session: AsyncSession,
) -> None:
    """`accounts.ensure` has the same race one level down: two tabs loading a
    balance at once both find nothing and both insert."""
    user_id = uuid.uuid4()

    async def ensure_once() -> uuid.UUID:
        async with get_session_factory()() as s:
            account = await accounts.ensure(s, AccountKind.USER, user_id)
            await s.commit()
            return account.id

    ids = await asyncio.gather(*(ensure_once() for _ in range(6)))

    assert len(set(ids)) == 1
