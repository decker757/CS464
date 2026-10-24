"""The void against a trade, a placement, a settlement, another void and an
administrator's cancel. [BE] #221, ADR 0015, ADR 0025.

Shaped as `test_settlement_concurrency.py`: every party opens its own session,
connects, and waits on an `asyncio.Barrier`, then the order is forced. One
party pauses holding the book lock, and the next starts only once
`pg_stat_activity` shows it queued there. Every wait fails by name after a
timeout rather than hanging, and each test names the line whose removal turns
it red. Statuses are never enough: every test counts what was written.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.placement_fixtures import order_row, place
from unit_test.settlement_fixtures import (
    Pause,
    admin,
    book_lock_waiters,
    finish,
    raise_any,
    reached,
    result_count,
    settle,
)
from unit_test.trade_fixtures import (
    SMALL_QUANTITY,
    assert_ledger_balances,
    books,
    buy,
    entities,
    errors,
    fund,
    pool_balance,
    session_factory,
    trading,
    transaction_count,
)
from unit_test.void_fixtures import (
    VoidUpstream,
    figures,
    gate_open_view,
    open_book,
    refunds_by_holder,
    trade,
    void,
    voided_entries,
)

FLAT_B = Decimal("100.0000")
FLAT_SEED = Decimal("250.0000")


async def _traded_market(session: AsyncSession) -> tuple[VoidUpstream, uuid.UUID]:
    """A market one holder has bought into, closed and voidable."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=0, side="buy", quantity="5")
    upstream.closes()
    return upstream, holder


async def _version(session: AsyncSession, market_id: uuid.UUID) -> int:
    book = entities().MarketBook
    session.expire_all()
    return (
        await session.execute(select(book.state_version).where(book.market_id == market_id))
    ).scalar_one()


async def _settlement_count(session: AsyncSession) -> int:
    transaction = entities().Transaction
    return (
        await session.execute(
            select(func.count())
            .select_from(transaction)
            .where(
                transaction.kind.in_(
                    [
                        entities().TransactionKind.SETTLEMENT,
                        entities().TransactionKind.SETTLEMENT_RESIDUE,
                    ]
                )
            )
        )
    ).scalar_one()


async def test_a_trade_queued_behind_a_void_is_refused_and_writes_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0025, "Void against trade": the trade's gate stubbed open, so only
    the latch and the book lock can refuse it. The void pauses after its
    lookup under the book lock, holding the lock with nothing written; the
    trade, funded and quoting the current version, is seen queued on the
    book lock; then the void is released.

    - Remove the trade path's `market_results` lookup under the book lock: the
      trade commits, so its key has a transaction.
    - Make that lookup filter on `kind = 'settled'`: the same.
    """
    upstream, _ = await _traded_market(session)
    trader = uuid.uuid4()
    await fund(session, trader)
    version = await _version(session, upstream.market_id)

    factory = session_factory()
    start = asyncio.Barrier(2)
    voiding, trading_session = factory(), factory()
    pause = Pause(monkeypatch, books(), "find_result", session=voiding, call=2)

    async def run_void():
        await voiding.connection()
        await start.wait()
        return await void(voiding, upstream)

    async def run_trade():
        await trading_session.connection()
        await start.wait()
        await pause.reached.wait()
        try:
            await buy(
                trading_session,
                upstream,
                user_id=trader,
                outcome=1,
                quantity=SMALL_QUANTITY,
                state_version=version,
                client_key="racing-the-void",
                transport=upstream.gate_open,
            )
        except errors().MarketClosed:
            return "market_closed"
        return "traded"

    try:
        tasks = [asyncio.create_task(run_void()), asyncio.create_task(run_trade())]
        try:
            await reached(pause.reached, "the void never held the book lock", tasks)
            await book_lock_waiters(1, "the trade never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await voiding.close()
        await trading_session.close()

    raise_any(outcomes)
    assert outcomes[1] == "market_closed"
    key = trading().trade_key(trader, upstream.market_id, "racing-the-void")
    assert await transaction_count(session, key=key) == 0, "the queued trade wrote"
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    await assert_ledger_balances(session)


async def test_a_placement_queued_behind_a_void_is_refused_market_closed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0025, "Void against placement": the placement's gate stubbed open,
    so only its latch under the book lock can refuse it. It queues behind the
    void and is `409 market_closed`, with no order written. Red with the
    placement's latch (`orders.place`'s `books.find_result`) removed."""
    from unit_test.placement_fixtures import order_count  # noqa: PLC0415

    upstream, _ = await _traded_market(session)
    owner = uuid.uuid4()
    await fund(session, owner)
    orders_before = await order_count(session)

    factory = session_factory()
    start = asyncio.Barrier(2)
    voiding, placing = factory(), factory()
    pause = Pause(monkeypatch, books(), "find_result", session=voiding, call=2)

    async def run_void():
        await voiding.connection()
        await start.wait()
        return await void(voiding, upstream)

    async def run_placement():
        await placing.connection()
        await start.wait()
        await pause.reached.wait()
        try:
            await place(
                placing,
                gate_open_view(upstream),
                user_id=owner,
                outcome=1,
                limit_price=Decimal("0.1000"),
                quantity=Decimal("5.0000"),
            )
        except errors().MarketClosed:
            return "market_closed"
        return "placed"

    try:
        tasks = [asyncio.create_task(run_void()), asyncio.create_task(run_placement())]
        try:
            await reached(pause.reached, "the void never held the book lock", tasks)
            await book_lock_waiters(1, "the placement never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await voiding.close()
        await placing.close()

    raise_any(outcomes)
    assert outcomes[1] == "market_closed"
    assert await order_count(session) == orders_before


async def test_a_placement_that_took_the_lock_first_is_in_the_replay_and_released(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0025, the other order: a placement that took the book lock first
    commits first. On a book at `q = (90, 0)`, `b = 100`, a resting buy of
    100 outcome-1 shares at 0.4500 fills in part on arrival; the void, queued
    behind it, refunds that fill and releases the rest. Remove the void's
    `books.lock_book`: it never queues, and the wait for it times out."""
    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    await open_book(session, upstream)
    await trade(session, upstream, uuid.uuid4(), outcome=0, side="buy", quantity="90")
    owner = uuid.uuid4()
    await fund(session, owner)
    upstream.closes()

    factory = session_factory()
    start = asyncio.Barrier(2)
    placing, voiding = factory(), factory()
    pause = Pause(monkeypatch, books(), "lock_book", session=placing, call=1)

    async def run_placement():
        await placing.connection()
        await start.wait()
        return await place(
            placing,
            gate_open_view(upstream),
            user_id=owner,
            outcome=1,
            limit_price=Decimal("0.4500"),
            quantity=Decimal("100.0000"),
        )

    async def run_void():
        await voiding.connection()
        await start.wait()
        await pause.reached.wait()
        return await void(voiding, upstream)

    try:
        tasks = [asyncio.create_task(run_placement()), asyncio.create_task(run_void())]
        try:
            await reached(pause.reached, "the placement never held the book lock", tasks)
            await book_lock_waiters(1, "the void never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await placing.close()
        await voiding.close()

    raise_any(outcomes)
    placed, _ = outcomes
    filled = await order_row(session, placed.order.id)
    filled_quantity, filled_value = filled.filled_quantity, filled.filled_value
    assert filled_quantity > 0, "the placement was meant to fill in part"
    refunds = await refunds_by_holder(session, upstream.market_id)
    assert refunds[(owner, upstream.outcomes[1])] == filled_value
    from unit_test.placement_fixtures import reserved_credits_of  # noqa: PLC0415

    assert await reserved_credits_of(session, owner) == Decimal("0.0000")
    await assert_ledger_balances(session)


def _settleable_and_voidable(upstream: VoidUpstream) -> None:
    upstream.body.update(
        status="approved",
        settleable=True,
        voidable=True,
        proposed_outcome_id=str(upstream.outcomes[0]),
    )


@pytest.mark.parametrize("first", ["void", "settlement"])
async def test_a_void_and_a_settlement_end_the_market_once(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    """ADR 0025, "Void against settlement", on a market stubbed both voidable
    and settleable. Whichever takes the book lock first writes its row and
    its money; the other, queued behind it, finds the row under the lock and
    writes nothing: the settlement `409 market_already_voided`, the void
    `409 market_already_settled`. Exactly one result row, and one kind of
    money movement.

    Remove the lookup after the book lock from the party that queues: it
    writes beside the first, and its record collides on the primary key.
    """
    upstream, holder = await _traded_market(session)
    _settleable_and_voidable(upstream)

    factory = session_factory()
    start = asyncio.Barrier(2)
    first_session, second_session = factory(), factory()
    pause = Pause(monkeypatch, books(), "find_result", session=first_session, call=2)

    def party(name: str, own: AsyncSession):
        return void(own, upstream) if name == "void" else settle(own, upstream)

    second = "settlement" if first == "void" else "void"
    refusal = (
        errors().MarketAlreadyVoided if second == "settlement" else errors().MarketAlreadySettled
    )

    async def run_first():
        await first_session.connection()
        await start.wait()
        return await party(first, first_session)

    async def run_second():
        await second_session.connection()
        await start.wait()
        await pause.reached.wait()
        try:
            await party(second, second_session)
        except refusal:
            return "refused"
        return "wrote"

    try:
        tasks = [asyncio.create_task(run_first()), asyncio.create_task(run_second())]
        try:
            await reached(pause.reached, f"the {first} never held the book lock", tasks)
            await book_lock_waiters(1, f"the {second} never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await first_session.close()
        await second_session.close()

    raise_any(outcomes)
    assert outcomes[1] == "refused"
    assert await result_count(session, upstream.market_id) == 1
    refunds = await refunds_by_holder(session, upstream.market_id)
    settlements = await _settlement_count(session)
    if first == "void":
        assert (len(refunds), settlements) == (1, 0)
    else:
        assert (refunds, settlements > 0) == ({}, True)
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    await assert_ledger_balances(session)


async def test_two_voids_refund_once_and_answer_alike(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, audit_reader: AsyncSession
) -> None:
    """ADR 0025, "Void against void": one writes, both answer `200` with the
    same body. A pauses after its lookup under the lock; B's unlocked lookup
    misses, and B is seen queued, then A is released. Remove the lookup after
    the book lock: B reaches `post_all` with its record staged and is refused
    `pending_writes_on_replay`."""
    upstream, holder = await _traded_market(session)
    first, second = admin(), admin(username="ihsan_k")

    factory = session_factory()
    start = asyncio.Barrier(2)
    session_a, session_b = factory(), factory()
    pause = Pause(monkeypatch, books(), "find_result", session=session_a, call=2)

    async def run_a():
        await session_a.connection()
        await start.wait()
        return await void(session_a, upstream, actor=first)

    async def run_b():
        await session_b.connection()
        await start.wait()
        await pause.reached.wait()
        return await void(session_b, upstream, actor=second)

    try:
        tasks = [asyncio.create_task(run_a()), asyncio.create_task(run_b())]
        try:
            await reached(pause.reached, "void A never held the book lock", tasks)
            await book_lock_waiters(1, "void B never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await session_a.close()
        await session_b.close()

    raise_any(outcomes)
    answer_a, answer_b = outcomes
    assert pause.results_for(session_b)[0] is None, (
        "B's unlocked lookup hit, so this never reached the locked re-check"
    )
    assert figures(answer_a) == figures(answer_b)
    assert len(await refunds_by_holder(session, upstream.market_id)) == 1
    assert len(await voided_entries(audit_reader, first.id, second.id)) == 1
    await assert_ledger_balances(session)


@pytest.mark.parametrize("first", ["cancel", "void"])
async def test_a_void_and_an_administrators_cancel_serialise_on_the_book(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_reader: AsyncSession,
    first: str,
) -> None:
    """ADR 0025, "Void against the administrator's cancel". The cancel first:
    the order is cancelled, with one `order.cancelled_by_admin` entry, and the
    void then voids. The void first: the cancel, queued behind it, finds the
    order released, `409 order_released`, and writes no entry. Remove the
    cancel's `books.lock_book`: it never queues behind the void, and the wait
    for it times out."""
    from service import cancellation  # noqa: PLC0415
    from service.cancellation import AdminCancel  # noqa: PLC0415

    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    await open_book(session, upstream)
    owner = uuid.uuid4()
    placed = await place(
        session,
        upstream,
        user_id=owner,
        outcome=1,
        limit_price=Decimal("0.1000"),
        quantity=Decimal("5.0000"),
    )
    upstream.closes()
    canceller = admin(username="ihsan_k")

    factory = session_factory()
    start = asyncio.Barrier(2)
    voiding, cancelling = factory(), factory()
    if first == "void":
        pause = Pause(monkeypatch, books(), "find_result", session=voiding, call=2)
    else:
        pause = Pause(monkeypatch, books(), "lock_book", session=cancelling, call=1)

    async def run_void():
        await voiding.connection()
        await start.wait()
        if first == "cancel":
            await pause.reached.wait()
        return await void(voiding, upstream)

    async def run_cancel():
        await cancelling.connection()
        await start.wait()
        if first == "void":
            await pause.reached.wait()
        try:
            await cancellation.cancel(
                cancelling,
                placed.order.id,
                user_id=None,
                admin=AdminCancel(actor=canceller, reason="A cancel racing the void."),
            )
        except errors().OrderReleased:
            return "order_released"
        return "cancelled"

    try:
        tasks = [asyncio.create_task(run_void()), asyncio.create_task(run_cancel())]
        try:
            await reached(pause.reached, f"the {first} never held the book lock", tasks)
            await book_lock_waiters(1, "the other party never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await voiding.close()
        await cancelling.close()

    raise_any(outcomes)
    from unit_test.settlement_fixtures import _audit_rows, _audit_table  # noqa: PLC0415

    table = _audit_table()
    cancels = await _audit_rows(
        audit_reader,
        table.c.action_type == "order.cancelled_by_admin",
        table.c.actor_id == canceller.id,
    )
    status = (await order_row(session, placed.order.id)).status.value
    if first == "cancel":
        assert (outcomes[1], status, len(cancels)) == ("cancelled", "cancelled", 1)
    else:
        assert (outcomes[1], status, len(cancels)) == ("order_released", "open", 0)
    assert await result_count(session, upstream.market_id) == 1
