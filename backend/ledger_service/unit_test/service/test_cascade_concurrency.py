"""Two trades, one book, both crossing the same resting orders. [T-7] #27,
ADR 0015, ADR 0024.

#27: "ledger-balance invariant holds under concurrent trading plus
triggering". ADR 0024's Tests owed: "Entries sum to zero per transaction and
overall under concurrent trading plus cascades, with balance at least the
reservation for every user and position at least reserved shares."

Each party opens its own session, connects and waits on an `asyncio.Barrier`.
The first trade then pauses inside its cascade, holding the book lock, just
after it has read the open orders; the second starts, and the test waits until
Postgres shows it queued on the book row, or until it has finished, which it
can only do without the lock.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Order, Transaction
from service import reservations, trading
from unit_test.placement_fixtures import limit_upstream, open_at, place, reserved_credits_of
from unit_test.sell_fixtures import assert_q_matches_positions, hold, holding
from unit_test.settlement_fixtures import (
    Pause,
    finish,
    queued_on_the_book_or_finished,
    reached,
)
from unit_test.trade_fixtures import (
    assert_ledger_balances,
    balance_of_user,
    buy,
    fund,
    retry_on_quote_stale,
    session_factory,
)

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))
EACH_TRADE = Decimal("80.0000")


async def _funded(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    await fund(session, user_id)
    return user_id


async def _fill_totals(session: AsyncSession, order_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    """What the order's fill transactions moved: shares, and credits signed
    positive, the two figures its rollups have to equal."""
    session.expire_all()
    contexts = (
        await session.execute(
            select(Transaction.context).where(
                Transaction.context["order_id"].astext == str(order_id)
            )
        )
    ).scalars()
    shares = credits = Decimal(0)
    for context in contexts:
        shares += Decimal(context["quantity"])
        credits += abs(Decimal(context["total"]))
    return shares, credits


async def test_two_trades_crossing_the_same_orders_keep_every_invariant(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resting sell of outcome 0 and a resting buy of outcome 1, by two
    owners, both below what one buy of 80 outcome-0 shares takes the price to.
    Two traders send that buy at once, quoting the same version. The first
    fills orders under the book lock; the second waits for it, is refused
    `quote_stale`, re-quotes, and its own cascade fills what is left.

    Remove the book lock from `execute` (`books.lock_book` read without
    `FOR UPDATE`): the second trade plans its cascade from the book as it was,
    its fills reuse the first trade's keys, and it fails instead of queuing.
    """
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    seller, buyer = await _funded(session), await _funded(session)
    traders = [await _funded(session), await _funded(session)]
    await hold(session, upstream, user_id=seller, quantity=Decimal("30.0000"))
    sell_order = await place(
        session,
        upstream,
        user_id=seller,
        side="sell",
        limit_price=Decimal("0.6200"),
        quantity=Decimal("20.0000"),
    )
    buy_order = await place(
        session,
        upstream,
        user_id=buyer,
        outcome=1,
        limit_price=Decimal("0.3500"),
        quantity=Decimal("50.0000"),
    )
    quoted = sell_order.book_state_version

    factory = session_factory()
    start = asyncio.Barrier(2)
    first_session, second_session = factory(), factory()
    pause = Pause(monkeypatch, trading, "list_open_orders", session=first_session, call=1)

    async def run(own: AsyncSession, trader: uuid.UUID, *, waits: bool):
        await own.connection()
        await start.wait()
        if waits:
            await pause.reached.wait()

        async def attempt(version: int):
            return await buy(
                own,
                upstream,
                user_id=trader,
                quantity=EACH_TRADE,
                state_version=version,
                client_key=f"race-{trader}",
            )

        return await retry_on_quote_stale(own, attempt, version=quoted, retries=3)

    try:
        first = asyncio.create_task(run(first_session, traders[0], waits=False))
        second = asyncio.create_task(run(second_session, traders[1], waits=True))
        tasks = [first, second]
        try:
            await reached(pause.reached, "the first trade never reached its cascade", tasks)
            await queued_on_the_book_or_finished(second)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await first_session.close()
        await second_session.close()

    for outcome in outcomes:
        assert not isinstance(outcome, BaseException), outcome
    assert [result.book_state_version > result.state_version for result in outcomes] == [
        True,
        True,
    ]

    await assert_ledger_balances(session)
    await assert_q_matches_positions(session, upstream.market_id)
    for user_id in [seller, buyer, *traders]:
        assert await balance_of_user(session, user_id) >= await reserved_credits_of(
            session, user_id
        )
    held, _ = await holding(session, seller, upstream.market_id, upstream.outcomes[0])
    assert held >= await reservations.reserved_shares_of(
        session, seller, market_id=upstream.market_id, outcome_id=upstream.outcomes[0]
    )
    for order_id in [sell_order.order.id, buy_order.order.id]:
        totals = await _fill_totals(session, order_id)
        rollups = (
            await session.execute(
                select(Order.filled_quantity, Order.filled_value).where(Order.id == order_id)
            )
        ).one()
        assert totals == tuple(rollups)
