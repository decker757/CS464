"""Settlement against a trade, against itself, and behind a trade's retry.
[3.4] #12, ADR 0015, ADR 0019.

Every party opens its own session, connects, and waits on an
`asyncio.Barrier` (ADR 0015). The barrier alone does not order them: both
paths roll back before their HTTP calls, which returns the connection the
barrier was waiting on. So each test also forces its order. One party pauses
holding the book lock, and the next starts only once `pg_stat_activity` shows
it queued on that lock. Every wait fails by name after a timeout rather than
hanging, and each test names the line whose removal turns it red.

Statuses are never enough here: every test counts entries.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.settlement_fixtures import (
    LOSER,
    WINNER,
    Pause,
    SettleUpstream,
    admin,
    book_lock_waiters,
    books,
    errors,
    finish,
    hold,
    payout_key,
    raise_any,
    reached,
    residue_key,
    session_factory,
    settle,
    settled_entries,
    warm,
)
from unit_test.trade_fixtures import (
    SMALL_QUANTITY,
    assert_ledger_balances,
    book_row,
    buy,
    fund,
    pool_balance,
    trading,
    transaction_count,
)

WINNING = Decimal("41.3337")
LOSING = Decimal("17.0429")


async def _market_with_holders(session: AsyncSession) -> SettleUpstream:
    upstream = SettleUpstream()
    await warm(session, upstream)
    await hold(
        session,
        upstream,
        [(uuid.uuid4(), WINNER, WINNING), (uuid.uuid4(), LOSER, LOSING)],
    )
    return upstream


async def test_a_trade_queued_behind_a_settlement_is_refused_and_the_pool_ends_empty(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#12: "A trade on a settled market is refused `409 market_closed`,
    decided under the book lock", and its test note: the gate is stubbed open,
    so only the latch and the book lock can refuse the trade.

    The settlement pauses after its under-lock lookup misses, holding the
    book lock with nothing written. The trade starts, funded and quoting the
    current `state_version`, so neither the gate, the staleness check nor an
    overdraft can refuse it, and is seen queued on the book lock. Then the
    settlement is released.

    - Remove the trade path's `market_results` lookup under the book lock:
      the trade commits, so its key has an entry and the pool ends above zero.
    - Remove settlement's `books.lock_book`: the trade never queues, and the
      wait for it times out.
    """
    upstream = await _market_with_holders(session)
    trader = uuid.uuid4()
    await fund(session, trader)
    version = (await book_row(session, upstream.market_id)).state_version

    factory = session_factory()
    start = asyncio.Barrier(2)
    settling, trading_session = factory(), factory()
    pause = Pause(monkeypatch, books(), "find_result", session=settling, call=2)

    async def run_settlement():
        await settling.connection()
        await start.wait()
        return await settle(settling, upstream)

    async def run_trade():
        await trading_session.connection()
        await start.wait()
        await pause.reached.wait()
        try:
            await buy(
                trading_session,
                upstream,
                user_id=trader,
                outcome=WINNER,
                quantity=SMALL_QUANTITY,
                state_version=version,
                client_key="racing-the-settlement",
                transport=upstream.gate_open,
            )
        except errors().MarketClosed:
            return "market_closed"
        return "traded"

    try:
        tasks = [asyncio.create_task(run_settlement()), asyncio.create_task(run_trade())]
        try:
            await reached(pause.reached, "the settlement never held the book lock", tasks)
            await book_lock_waiters(1, "the trade never queued on the book lock", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached)
    finally:
        await settling.close()
        await trading_session.close()

    raise_any(outcomes)
    settled, traded = outcomes
    assert traded == "market_closed"
    key = trading().trade_key(trader, upstream.market_id, "racing-the-settlement")
    assert await transaction_count(session, key=key) == 0, "the queued trade wrote"
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    assert settled.holders_paid == 1
    await assert_ledger_balances(session)


async def test_two_settlements_pay_once_and_answer_alike(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_reader: AsyncSession,
) -> None:
    """#12's settle-against-settle note: one writes, both answer 200 with the
    same settlement.

    A pauses after its under-lock lookup, holding the lock with nothing
    committed. B's unlocked lookup therefore misses (asserted, so B cannot
    replay early through the wrong lookup), and B is seen queued on the book
    lock. Then A is released.

    Remove the `market_results` lookup issued after the book lock statement:
    B reaches `post_all` with its record staged and is refused
    `pending_writes_on_replay`.
    """
    upstream = await _market_with_holders(session)
    first, second = admin(), admin(username="ihsan_k")

    factory = session_factory()
    start = asyncio.Barrier(2)
    session_a, session_b = factory(), factory()
    pause = Pause(monkeypatch, books(), "find_result", session=session_a, call=2)

    async def run_a():
        await session_a.connection()
        await start.wait()
        return await settle(session_a, upstream, actor=first)

    async def run_b():
        await session_b.connection()
        await start.wait()
        await pause.reached.wait()
        return await settle(session_b, upstream, actor=second)

    try:
        tasks = [asyncio.create_task(run_a()), asyncio.create_task(run_b())]
        try:
            await reached(pause.reached, "settlement A never held the book lock", tasks)
            await book_lock_waiters(1, "settlement B never queued on the book lock", tasks)
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
    assert answer_a == answer_b
    for user_id in await _winners(session, upstream):
        assert (
            await transaction_count(
                session, key=payout_key(upstream.market_id, user_id)
            )
            == 1
        )
    assert await transaction_count(session, key=residue_key(upstream.market_id)) == 1
    assert len(await settled_entries(audit_reader, first.id, second.id)) == 1
    assert upstream.posts == 2
    await assert_ledger_balances(session)


async def _winners(session: AsyncSession, upstream: SettleUpstream) -> list[uuid.UUID]:
    from sqlalchemy import select  # noqa: PLC0415

    from model.entities import Position  # noqa: PLC0415

    session.expire_all()
    return list(
        (
            await session.execute(
                select(Position.user_id).where(
                    Position.market_id == upstream.market_id,
                    Position.outcome_id == upstream.winner,
                    Position.quantity > 0,
                )
            )
        ).scalars()
    )


async def test_a_trade_retried_behind_a_settlement_replays_its_original(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DECISIONS.md, "The trade latch sits after the key re-check under the
    book lock": a trade that committed before the settlement still replays,
    so a trader who was charged is never told they were refused.

    T1 holds the book lock, uncommitted. The settlement S queues behind it
    and is seen waiting before T1's retry T2 starts. T2, its gate stubbed
    open, is seen queued behind S. Then T1 is released: it commits, S settles
    (paying T1's shares), and T2 finds T1's committed transaction.

    - Put the latch ahead of the key re-check: T2 is refused `409`.
    - Read settlement's clock before its book lock: S's `recorded_at` falls
      before T1's `state_changed_at`, which T1 reads after the release.
    """
    upstream = await _market_with_holders(session)
    trader = uuid.uuid4()
    await fund(session, trader)

    factory = session_factory()
    start = asyncio.Barrier(3)
    session_t1, session_s, session_t2 = factory(), factory(), factory()
    pause = Pause(monkeypatch, books(), "lock_book", session=session_t1, call=1)
    settlement_queued = asyncio.Event()
    finished: list[str] = []

    async def trade(own: AsyncSession, name: str, *, wait_for: asyncio.Event | None):
        await own.connection()
        await start.wait()
        if wait_for is not None:
            await wait_for.wait()
        try:
            result = await buy(
                own,
                upstream,
                user_id=trader,
                outcome=WINNER,
                quantity=SMALL_QUANTITY,
                state_version=0,
                client_key="retried-trade",
                transport=upstream.gate_open,
            )
        except errors().MarketClosed:
            result = "market_closed"
        finished.append(name)
        return result

    async def run_settlement():
        await session_s.connection()
        await start.wait()
        await pause.reached.wait()
        result = await settle(session_s, upstream)
        finished.append("S")
        return result

    try:
        tasks = [
            asyncio.create_task(trade(session_t1, "T1", wait_for=None)),
            asyncio.create_task(run_settlement()),
            asyncio.create_task(trade(session_t2, "T2", wait_for=settlement_queued)),
        ]
        try:
            await reached(pause.reached, "T1 never held the book lock", tasks)
            await book_lock_waiters(1, "the settlement never queued behind T1", tasks)
            settlement_queued.set()
            await book_lock_waiters(2, "T2 never queued behind the settlement", tasks)
        finally:
            outcomes = await finish(tasks, pause.release, pause.reached, settlement_queued)
    finally:
        for own in (session_t1, session_s, session_t2):
            await own.close()

    raise_any(outcomes)
    original, settled, retried = outcomes
    assert finished.index("S") < finished.index("T2"), (
        "the book lock went to T2 before the settlement; this proves nothing"
    )
    assert retried == original, "a charged trade's retry was not replayed"
    key = trading().trade_key(trader, upstream.market_id, "retried-trade")
    assert await transaction_count(session, key=key) == 1
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    traded_at = (await book_row(session, upstream.market_id)).state_changed_at
    assert settled.recorded_at > traded_at, (
        "the settlement that queued behind the trade is stamped before it"
    )
    await assert_ledger_balances(session)
