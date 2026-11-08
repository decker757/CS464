"""The per-market and process-wide limits on trades holding a connection. #667

Pure: no database. What the limits do to real connections is in
`unit_test/service/test_connection_slots.py`. The first two tests here are
what that file cannot see: the map's size, and the order the two limits are
taken in. The last two pin each limit on its own, without Postgres.
DECISIONS.md, "Only a few trades per market hold a ledger connection at
once".
"""

from __future__ import annotations

import asyncio
import uuid

from core.connection_slots import ConnectionSlots

# Long enough for a task that could enter to have entered.
_SETTLE_SECONDS = 0.05


async def _hold_until(
    slots: ConnectionSlots,
    market_id: uuid.UUID,
    *,
    entered: asyncio.Event,
    leave: asyncio.Event,
) -> None:
    async with slots.hold(market_id):
        entered.set()
        await leave.wait()


async def test_a_market_nobody_holds_or_waits_for_is_forgotten() -> None:
    """The map holds a semaphore only while a request holds or waits for one,
    so a process that has seen every market keeps none of them. Remove the
    clean-up and the map grows by one per market traded, forever."""
    slots = ConnectionSlots(per_market=1, total=4)
    market_id = uuid.uuid4()
    holder_entered, holder_leaves = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(
        _hold_until(slots, market_id, entered=holder_entered, leave=holder_leaves)
    )
    await holder_entered.wait()
    waiter = asyncio.create_task(
        _hold_until(slots, market_id, entered=asyncio.Event(), leave=asyncio.Event())
    )
    await asyncio.sleep(_SETTLE_SECONDS)
    assert slots.markets_in_flight == 1

    # A waiter that is cancelled takes its count with it, and the holder still
    # keeps the market.
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert slots.markets_in_flight == 1

    holder_leaves.set()
    await holder
    assert slots.markets_in_flight == 0


async def test_a_request_waiting_for_its_market_takes_no_process_wide_slot() -> None:
    """The market's limit is taken first, so a hot market's queue waits
    outside the process-wide limit and a trade on another market still gets
    in. Take the two the other way round and the hot market's waiters sit on
    the process-wide slots, and the other market's trade waits behind them."""
    slots = ConnectionSlots(per_market=1, total=2)
    hot, quiet = uuid.uuid4(), uuid.uuid4()
    leave = asyncio.Event()
    holder_entered = asyncio.Event()
    tasks = [asyncio.create_task(_hold_until(slots, hot, entered=holder_entered, leave=leave))]
    await holder_entered.wait()
    tasks += [
        asyncio.create_task(_hold_until(slots, hot, entered=asyncio.Event(), leave=leave))
        for _ in range(3)
    ]
    await asyncio.sleep(_SETTLE_SECONDS)

    quiet_entered = asyncio.Event()
    tasks.append(
        asyncio.create_task(_hold_until(slots, quiet, entered=quiet_entered, leave=leave))
    )
    try:
        async with asyncio.timeout(1):
            await quiet_entered.wait()
    finally:
        leave.set()
        await asyncio.gather(*tasks)


async def test_no_more_than_the_process_wide_limit_hold_at_once() -> None:
    """Across markets, the process-wide limit is the bound: four markets at
    a per-market limit of two would be eight, and only three get in."""
    slots = ConnectionSlots(per_market=2, total=3)
    leave = asyncio.Event()
    entered = [asyncio.Event() for _ in range(8)]
    markets = [uuid.uuid4() for _ in range(4)]
    tasks = [
        asyncio.create_task(
            _hold_until(slots, markets[i % 4], entered=entered[i], leave=leave)
        )
        for i in range(8)
    ]
    await asyncio.sleep(_SETTLE_SECONDS)
    inside = sum(event.is_set() for event in entered)

    leave.set()
    await asyncio.gather(*tasks)
    assert inside == 3


async def test_no_more_than_the_per_market_limit_hold_one_market_at_once() -> None:
    """On one market, the per-market limit is the bound, however many
    process-wide slots are free."""
    slots = ConnectionSlots(per_market=2, total=10)
    market_id = uuid.uuid4()
    leave = asyncio.Event()
    entered = [asyncio.Event() for _ in range(5)]
    tasks = [
        asyncio.create_task(_hold_until(slots, market_id, entered=event, leave=leave))
        for event in entered
    ]
    await asyncio.sleep(_SETTLE_SECONDS)
    inside = sum(event.is_set() for event in entered)

    leave.set()
    await asyncio.gather(*tasks)
    assert inside == 2
