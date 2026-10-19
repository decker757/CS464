"""A cancel racing a fill, in both orderings, plus the partial case. [T-8] #28,
ADR 0015, ADR 0024.

#28: "Cancel racing a fill resolves deterministically (both go through the
market's serialized path): whichever commits first wins, the loser gets a clear
'already filled' or 'already cancelled' response", and "Never a double-spend,
never a stuck reservation". ADR 0024's Tests owed: "A cancel racing a fill, in
both orderings, plus the partial case."

The market is `b = 100` at `q = [0, 0]`. The owner rests a buy of outcome 0 at
0.4000, and a trader buys 50 shares of outcome 1, which takes outcome 0 to
0.37754 and makes the order executable. Its fill is
`k = 100·ln(0.4 / 0.6) + 50 = 9.45348…`, floored to 9.4534 and capped at the
order's remainder. Five shares fill completely for 1.9172
(`C([5, 50]) - C([0, 50]) = 1.91719…`, rounded up); twenty fill 9.4534 for
3.6749 (`3.67482…`), and of their 8.0000 budget 4.3251 is left. Worked with
stdlib `Decimal` at 60 digits, sharing nothing with `core/`.

Each party opens its own session, connects and waits on an `asyncio.Barrier`.
The first then pauses holding the book lock: the trade inside its cascade, just
after it has read the open orders, or the cancel just after it has read the
order under the lock. The test waits until Postgres shows the other party
queued on the book row, or until it has finished, which it can only do without
the lock. Each test names what its assertions see when the cancel's
`books.lock_book` is removed.

An administrator's cancel racing the owner's ([T-8] #326) is at the end, in
both orderings, the same way.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import OrderAlreadyCancelled, OrderAlreadyFilled
from model.entities import OrderStatus, Transaction
from service import cancellation, trading
from unit_test.placement_fixtures import (
    limit_upstream,
    open_at,
    order_row,
    place,
    reserved_credits_of,
)
from unit_test.sell_fixtures import assert_q_matches_positions
from unit_test.settlement_fixtures import (
    Pause,
    admin,
    entries_naming,
    finish,
    queued_on_the_book_or_finished,
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
LIMIT = Decimal("0.4000")
TRADE = Decimal("50.0000")

WHOLE = Decimal("5.0000")
WHOLE_COST = Decimal("1.9172")

PART = Decimal("20.0000")
PART_BUDGET = Decimal("8.0000")
PART_FILL = Decimal("9.4534")
PART_COST = Decimal("3.6749")
PART_LEFT = Decimal("4.3251")

ADMIN_REASON = "The owner is suspended; releasing what the order holds."


async def _resting_buy(session: AsyncSession, quantity: Decimal):
    """The market, the order's owner, a funded trader, the resting order's
    id, and the version the trader quotes. Committed."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    owner, trader = uuid.uuid4(), uuid.uuid4()
    await fund(session, owner)
    await fund(session, trader)
    placed = await place(session, upstream, user_id=owner, limit_price=LIMIT, quantity=quantity)
    assert placed.fills == []
    return upstream, owner, trader, placed.order.id, placed.book_state_version


async def _trade(own: AsyncSession, upstream, *, trader: uuid.UUID, version: int):
    """The trader's buy of outcome 1, which takes outcome 0 below the limit."""
    return await buy(
        own,
        upstream,
        user_id=trader,
        outcome=1,
        quantity=TRADE,
        state_version=version,
        client_key=f"race-{trader}",
    )


async def _cancel(own: AsyncSession, order_id: uuid.UUID, *, owner: uuid.UUID):
    """The cancel's answer: what it released, or the code it was refused with."""
    try:
        return await cancellation.cancel(own, order_id, user_id=owner)
    except OrderAlreadyFilled as refusal:
        return refusal.code


async def _fills_of(session: AsyncSession, order_id: uuid.UUID) -> int:
    session.expire_all()
    stmt = select(func.count()).where(Transaction.context["order_id"].astext == str(order_id))
    return (await session.execute(stmt)).scalar_one()


async def _fill_then_cancel(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, quantity: Decimal
):
    """The trade takes the book lock first and pauses in its cascade; the
    cancel is seen queued behind it; then the trade is released. Returns the
    market, the owner, the order and both answers, the trade's first."""
    upstream, owner, trader, order_id, version = await _resting_buy(session, quantity)

    factory = session_factory()
    start = asyncio.Barrier(2)
    trading_session, cancelling = factory(), factory()
    pause = Pause(monkeypatch, trading, "_list_open_orders", session=trading_session, call=1)

    async def run_trade():
        await trading_session.connection()
        await start.wait()
        return await _trade(trading_session, upstream, trader=trader, version=version)

    async def run_cancel():
        await cancelling.connection()
        await start.wait()
        await pause.reached.wait()
        return await _cancel(cancelling, order_id, owner=owner)

    try:
        trade_task = asyncio.create_task(run_trade())
        cancel_task = asyncio.create_task(run_cancel())
        tasks = [trade_task, cancel_task]
        try:
            await reached(pause.reached, "the trade never reached its cascade", tasks)
            await queued_on_the_book_or_finished(cancel_task)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await trading_session.close()
        await cancelling.close()
    return upstream, owner, order_id, outcomes


async def test_a_fill_that_takes_the_book_first_leaves_the_cancel_order_already_filled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, starting_credits: Decimal
) -> None:
    """The fill commits first and exhausts the order, so the cancel, reading
    the order again under the book lock, finds nothing to release.

    Remove the cancel's `books.lock_book`: the cancel reads the order open and
    commits while the trade is paused, and answers with a release instead of
    `order_already_filled`; the trade then fails writing a fill over a
    cancelled row.
    """
    upstream, owner, order_id, (traded, cancelled) = await _fill_then_cancel(
        session, monkeypatch, WHOLE
    )

    assert cancelled == "order_already_filled"
    assert not isinstance(traded, BaseException), traded
    assert traded.book_state_version > traded.state_version
    row = await order_row(session, order_id)
    assert (row.status, row.filled_quantity, row.filled_value, row.cancelled_at) == (
        OrderStatus.FILLED,
        WHOLE,
        WHOLE_COST,
        None,
    )
    assert await balance_of_user(session, owner) == starting_credits - WHOLE_COST
    assert await reserved_credits_of(session, owner) == Decimal(0)
    await assert_q_matches_positions(session, upstream.market_id)
    await assert_ledger_balances(session)


async def test_a_partial_fill_that_takes_the_book_first_leaves_the_cancel_the_smaller_remainder(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, starting_credits: Decimal
) -> None:
    """The contract: "If a fill got there first, the cancel releases the
    smaller remainder". The fill spends 3.6749 of the 8.0000 budget, so the
    cancel releases 4.3251, and the owner's balance has paid for the fill and
    reserves nothing.

    Remove the cancel's `books.lock_book`: the cancel commits first and
    releases the whole 8.0000, and the fill then lands on the cancelled order.
    """
    upstream, owner, order_id, (traded, cancelled) = await _fill_then_cancel(
        session, monkeypatch, PART
    )

    assert not isinstance(cancelled, BaseException), cancelled
    assert cancelled.released_credits == PART_LEFT
    assert cancelled.order.filled_quantity == PART_FILL
    assert not isinstance(traded, BaseException), traded
    row = await order_row(session, order_id)
    assert (row.status, row.filled_quantity, row.filled_value) == (
        OrderStatus.CANCELLED,
        PART_FILL,
        PART_COST,
    )
    assert await balance_of_user(session, owner) == starting_credits - PART_COST
    assert await reserved_credits_of(session, owner) == Decimal(0)
    await assert_q_matches_positions(session, upstream.market_id)
    await assert_ledger_balances(session)


async def test_a_cancel_that_takes_the_book_first_leaves_the_order_unfilled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, starting_credits: Decimal
) -> None:
    """The contract: "If the cancel got there first, the order does not
    fill." The trade queues behind the cancel, then reads the open orders
    after the cancel committed, so its cascade finds nothing and it trades
    against the market maker alone. The whole budget is released.

    Remove the cancel's `books.lock_book`: the trade does not queue, its
    cascade fills 9.4534 shares of the order before the cancel writes, and
    the cancel still answers that it released the whole budget.
    """
    upstream, owner, trader, order_id, version = await _resting_buy(session, PART)

    factory = session_factory()
    start = asyncio.Barrier(2)
    cancelling, trading_session = factory(), factory()
    pause = Pause(monkeypatch, cancellation, "_read_order", session=cancelling, call=1)

    async def run_cancel():
        await cancelling.connection()
        await start.wait()
        return await _cancel(cancelling, order_id, owner=owner)

    async def run_trade():
        await trading_session.connection()
        await start.wait()
        await pause.reached.wait()
        return await _trade(trading_session, upstream, trader=trader, version=version)

    try:
        cancel_task = asyncio.create_task(run_cancel())
        trade_task = asyncio.create_task(run_trade())
        tasks = [cancel_task, trade_task]
        try:
            await reached(pause.reached, "the cancel never read the order", tasks)
            await queued_on_the_book_or_finished(trade_task)
        finally:
            cancelled, traded = await finish(tasks, pause.release, pause.reached)
    finally:
        await cancelling.close()
        await trading_session.close()

    assert not isinstance(cancelled, BaseException), cancelled
    assert cancelled.released_credits == PART_BUDGET
    assert not isinstance(traded, BaseException), traded
    assert traded.book_state_version == traded.state_version, "the cascade filled something"
    row = await order_row(session, order_id)
    assert (row.status, row.filled_quantity) == (OrderStatus.CANCELLED, Decimal(0))
    assert await _fills_of(session, order_id) == 0
    assert await balance_of_user(session, owner) == starting_credits
    assert await reserved_credits_of(session, owner) == Decimal(0)
    await assert_q_matches_positions(session, upstream.market_id)
    await assert_ledger_balances(session)


# =========================================================================
# An administrator's cancel racing the owner's. [T-8] #326
# =========================================================================
async def _cancel_or_refusal(
    own: AsyncSession,
    order_id: uuid.UUID,
    *,
    owner: uuid.UUID,
    by_admin: cancellation.AdminCancel | None,
):
    """One cancel's answer, the owner's when `by_admin` is None: what it
    released, or `order_already_cancelled`."""
    try:
        if by_admin is None:
            return await cancellation.cancel(own, order_id, user_id=owner)
        return await cancellation.cancel(own, order_id, user_id=None, admin=by_admin)
    except OrderAlreadyCancelled as refusal:
        return refusal.code


async def _admin_and_owner_cancel(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, *, admin_first: bool
):
    """Both cancel one resting order. The first takes the book lock and pauses
    just after reading the order under it; the second is seen queued on the
    book row; then the first is released. Returns the owner, the order and
    the answers, the administrator's first."""
    _, owner, _, order_id, _ = await _resting_buy(session, PART)
    by_admin = cancellation.AdminCancel(actor=admin(), reason=ADMIN_REASON)

    factory = session_factory()
    start = asyncio.Barrier(2)
    admin_session, owner_session = factory(), factory()
    first = admin_session if admin_first else owner_session
    pause = Pause(monkeypatch, cancellation, "_read_order", session=first, call=1)

    async def run(own: AsyncSession, cancelling_as: cancellation.AdminCancel | None):
        await own.connection()
        await start.wait()
        if own is not first:
            await pause.reached.wait()
        return await _cancel_or_refusal(own, order_id, owner=owner, by_admin=cancelling_as)

    try:
        admin_task = asyncio.create_task(run(admin_session, by_admin))
        owner_task = asyncio.create_task(run(owner_session, None))
        second_task = owner_task if admin_first else admin_task
        tasks = [admin_task, owner_task]
        try:
            await reached(pause.reached, "the first cancel never read the order", tasks)
            await queued_on_the_book_or_finished(second_task)
        finally:
            answers = await finish(tasks, pause.release, pause.reached)
    finally:
        await admin_session.close()
        await owner_session.close()
    return owner, order_id, answers


@pytest.mark.parametrize("admin_first", [True, False], ids=["admin_first", "owner_first"])
async def test_an_administrators_cancel_and_the_owners_cancel_of_one_order_release_it_once(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_reader: AsyncSession,
    starting_credits: Decimal,
    admin_first: bool,
) -> None:
    """#326: "Same book lock as the owner's cancel". Whichever takes the book
    first cancels and releases the whole 8.0000; the other, reading the order
    again under the lock, is `order_already_cancelled`. The audit entry is
    written only when the administrator won.

    Remove the cancel's `books.lock_book`: the second does not queue, reads
    the order still open, and commits a second cancel, so both answer with a
    release of 8.0000 and neither is refused.
    """
    owner, order_id, (by_admin, by_owner) = await _admin_and_owner_cancel(
        session, monkeypatch, admin_first=admin_first
    )

    winner, loser = (by_admin, by_owner) if admin_first else (by_owner, by_admin)
    assert loser == "order_already_cancelled"
    assert not isinstance(winner, BaseException), winner
    assert winner.released_credits == PART_BUDGET
    row = await order_row(session, order_id)
    assert (row.status, row.filled_quantity) == (OrderStatus.CANCELLED, Decimal(0))
    assert await balance_of_user(session, owner) == starting_credits
    assert await reserved_credits_of(session, owner) == Decimal(0)
    assert len(await entries_naming(audit_reader, order_id)) == (1 if admin_first else 0)
    await assert_ledger_balances(session)
