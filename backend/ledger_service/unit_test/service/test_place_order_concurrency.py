"""A resting placement racing a trade in another market. [T-6] #26, ADR 0015.

ADR 0024's Tests owed: "A resting placement in one market racing an instant
buy in another by the same user, with `asyncio.Barrier` and
`pg_stat_activity` lock waits, no timing. It must fail with the placement's
account lock removed."

The two share no book row, so the only lock they meet on is the user's
account row. Each party opens its own session, connects and waits on the
barrier. The trade then pauses inside `post_all`, holding the account lock,
just after it has read the user's reservation; the placement starts, and the
test waits until Postgres shows it queued on an account lock, or until it has
finished, which it can only do without the lock.

The second race is two placements with one key in one market: the duplicate
passes the unlocked retry lookup while the first holds the book row, and has
to be answered by the re-check under it.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from core.errors import InsufficientFunds
from model.entities import Transaction
from service import books, reservations
from unit_test.placement_fixtures import (
    limit_upstream,
    open_at,
    order_count,
    place,
    reserved_credits_of,
)
from unit_test.settlement_fixtures import (
    PROBE_TIMEOUT,
    Pause,
    book_lock_waiters,
    finish,
    reached,
)
from unit_test.trade_fixtures import (
    assert_ledger_balances,
    balance_of_user,
    buy,
    fund,
    session_factory,
)

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))

_ACCOUNT_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() "
    "AND usename = current_user "
    "AND wait_event_type = 'Lock' "
    "AND query ILIKE '%accounts%' "
    "AND query ILIKE '%for update%' "
    "AND pid <> pg_backend_pid()"
)


async def _queued_on_an_account_or_finished(placement: asyncio.Task) -> None:
    """Return once a backend waits on an account row lock or the placement has
    finished, whichever comes first. A fresh connection per poll, because
    `pg_stat_activity` is snapshotted once per transaction."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_TIMEOUT
    while not placement.done():
        async with get_engine().connect() as conn:
            if (await conn.execute(_ACCOUNT_LOCK_WAITERS)).scalar_one() == 1:
                return
        if loop.time() > deadline:
            pytest.fail("timed out: the placement neither queued nor finished")
        await asyncio.sleep(0.01)


async def test_a_resting_buy_racing_a_trade_in_another_market_cannot_both_spend(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, starting_credits: Decimal
) -> None:
    """The user's whole balance is the order's budget, so the order and the
    trade cannot both be paid for. The trade reads the reservation first and
    holds the account lock to its commit; the placement must wait for it, see
    the debit, and be refused.

    Remove `accounts.lock` from the resting placement: it reads the balance
    before the trade commits, both succeed, and the balance ends below the
    reservation.
    """
    placement_market, trade_market = limit_upstream(), limit_upstream()
    await open_at(session, placement_market, ZERO_Q)
    await open_at(session, trade_market, ZERO_Q)
    user_id = uuid.uuid4()
    await fund(session, user_id)
    # Rests: 0.4000 is below the price of 0.5000.
    limit = Decimal("0.4000")
    quantity = (starting_credits / limit).quantize(Decimal("0.0001"))

    factory = session_factory()
    start = asyncio.Barrier(2)
    trading_session, placing_session = factory(), factory()
    pause = Pause(monkeypatch, reservations, "reserved_of", session=trading_session, call=1)

    async def run_trade():
        await trading_session.connection()
        await start.wait()
        return await buy(
            trading_session,
            trade_market,
            user_id=user_id,
            quantity=Decimal("41.0000"),
            client_key="the-trade",
        )

    async def run_placement():
        await placing_session.connection()
        await start.wait()
        await pause.reached.wait()
        try:
            await place(
                placing_session,
                placement_market,
                user_id=user_id,
                limit_price=limit,
                quantity=quantity,
                client_key="the-order",
            )
        except InsufficientFunds:
            return "insufficient_funds"
        return "placed"

    try:
        trade = asyncio.create_task(run_trade())
        placement = asyncio.create_task(run_placement())
        tasks = [trade, placement]
        try:
            await reached(pause.reached, "the trade never read the reservation", tasks)
            await _queued_on_an_account_or_finished(placement)
        finally:
            traded, placed = await finish(tasks, pause.release, pause.reached)
    finally:
        await trading_session.close()
        await placing_session.close()

    assert not isinstance(traded, BaseException), traded
    assert placed == "insufficient_funds"
    balance = await balance_of_user(session, user_id)
    assert balance >= await reserved_credits_of(session, user_id)
    assert balance == starting_credits - Decimal("22.5867")
    await assert_ledger_balances(session)


# The contract's example market, `b = 100` at `q = [20, 0]`: five shares of
# outcome 0 at 0.6000 fill completely for C([25, 0]) - C([20, 0]) = 2.78005…,
# charged 2.7801 (`test_place_order.py`'s SMALL_COST).
DUPLICATE_LIMIT = Decimal("0.6000")
DUPLICATE_QUANTITY = Decimal("5.0000")
DUPLICATE_COST = Decimal("2.7801")


async def _fills_naming(session: AsyncSession, order_id: uuid.UUID) -> int:
    session.expire_all()
    stmt = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.context["order_id"].astext == str(order_id))
    )
    return (await session.execute(stmt)).scalar_one()


async def test_a_duplicate_placement_queued_on_the_book_lock_is_replayed_under_it(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, starting_credits: Decimal
) -> None:
    """Evidence for the under-lock key re-check in `orders.place`, in the one
    window where it is the only defence (ADR 0015).

    The first placement pauses holding the book row, uncommitted. The
    duplicate, the same request with the same key, starts then, so its
    unlocked retry lookup finds nothing, and the test waits until Postgres
    shows it queued on the book row. Released, the first commits one order
    and its fill, and the duplicate must answer with that placement.

    Remove the re-check after `books.lock_book`: the duplicate fills again at
    the moved price and dies on `uq_orders_idempotency_key`, a 500 where the
    contract promises the first order.
    """
    upstream = limit_upstream()
    await open_at(session, upstream)
    user_id = uuid.uuid4()
    await fund(session, user_id)

    factory = session_factory()
    start = asyncio.Barrier(2)
    first_session, duplicate_session = factory(), factory()
    pause = Pause(monkeypatch, books, "lock_book", session=first_session, call=1)

    async def run_placement(own: AsyncSession, *, wait_for_the_first: bool):
        await own.connection()
        await start.wait()
        if wait_for_the_first:
            await pause.reached.wait()
        return await place(
            own,
            upstream,
            user_id=user_id,
            limit_price=DUPLICATE_LIMIT,
            quantity=DUPLICATE_QUANTITY,
            client_key="one-order",
        )

    try:
        first = asyncio.create_task(run_placement(first_session, wait_for_the_first=False))
        duplicate = asyncio.create_task(
            run_placement(duplicate_session, wait_for_the_first=True)
        )
        tasks = [first, duplicate]
        try:
            await reached(pause.reached, "the first placement never locked the book", tasks)
            await book_lock_waiters(1, "the duplicate never queued on the book row", tasks)
        finally:
            placed, replayed = await finish(tasks, pause.release, pause.reached)
    finally:
        await first_session.close()
        await duplicate_session.close()

    assert not isinstance(placed, BaseException), placed
    assert not isinstance(replayed, BaseException), replayed
    assert replayed == placed
    assert placed.order.status == "filled"
    assert await order_count(session) == 1
    assert await _fills_naming(session, placed.order.id) == 1
    assert await balance_of_user(session, user_id) == starting_credits - DUPLICATE_COST
    await assert_ledger_balances(session)
