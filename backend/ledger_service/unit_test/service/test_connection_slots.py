"""On a hot market, only a few trades hold a connection, so reads and the
readiness check always find one. #667

DECISIONS.md, "Only a few trades per market hold a ledger connection at
once". The app's engine is sized as production sizes one ledger process
(`deploy/production/compose.yml`, 6 + 2), and the book locks are held from a
separate engine, so `pool.checkedout()` counts the trades' connections and
nothing else.

The parties meet at an `asyncio.Barrier` before they trade (ADR 0015), but do
not connect first, as the other race tests do: a session connected before the
trade holds the very connection these limits exist to withhold, and no
request connects before it reaches the trade path.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.database import dispose_engine, get_engine
from service import connection_slots
from shared.readiness import is_database_reachable, readiness_of
from unit_test.placement_fixtures import order_count, place
from unit_test.trade_fixtures import (
    BOOK_LOCK_WAITERS,
    PROBE_TIMEOUT,
    SMALL_QUANTITY,
    TIMEOUT,
    Recorder,
    Upstream,
    assert_ledger_balances,
    book_row,
    buy,
    errors,
    fund,
    session_factory,
    warm,
)

# deploy/production/compose.yml's ledger, and the settings' defaults.
_POOL_SIZE = 6
_MAX_OVERFLOW = 2
_PER_MARKET = 2
_FOR_READS = 2
_TRADE_CONNECTIONS = _POOL_SIZE + _MAX_OVERFLOW - _FOR_READS

# Long enough for a request the limits failed to hold back to have connected
# and queued on the book.
_SETTLE_SECONDS = 0.5

# A total far above what any of these trades costs, so a trade that waited is never
# refused for the price it moved to (#640); a placement this low rests.
_BUY_LIMIT = Decimal("100.0000")
_RESTING_LIMIT = Decimal("0.1000")

# How long a third request may take to commit while two publishes stall. A
# warm trade takes milliseconds.
_STALLED_PUBLISH_PATIENCE = 2.0

Submit = Callable[[AsyncSession, Upstream, uuid.UUID, str], Awaitable[object]]


async def _trade(own: AsyncSession, upstream: Upstream, user_id: uuid.UUID, key: str):
    return await buy(
        own,
        upstream,
        user_id=user_id,
        quantity=SMALL_QUANTITY,
        state_version=None,
        limit=_BUY_LIMIT,
        client_key=key,
    )


async def _placement(own: AsyncSession, upstream: Upstream, user_id: uuid.UUID, key: str):
    return await place(
        own,
        upstream,
        user_id=user_id,
        limit_price=_RESTING_LIMIT,
        quantity=SMALL_QUANTITY,
        client_key=key,
    )


@pytest.fixture
async def production_pool(clean_database, monkeypatch: pytest.MonkeyPatch):
    """The app's engine and limits at one production ledger's sizes, built
    fresh from them on first use."""
    settings = get_settings()
    await dispose_engine()
    monkeypatch.setattr(settings, "database_pool_size", _POOL_SIZE)
    monkeypatch.setattr(settings, "database_max_overflow", _MAX_OVERFLOW)
    monkeypatch.setattr(settings, "database_connections_per_market", _PER_MARKET)
    monkeypatch.setattr(settings, "database_connections_for_reads", _FOR_READS)
    monkeypatch.setattr(connection_slots, "_slots", None)
    yield
    await dispose_engine()


@pytest.fixture
async def outside_engine() -> AsyncIterator[AsyncEngine]:
    """An engine of its own, for the lock holder and the probes, so neither
    takes a connection from the app's pool or waits for one."""
    engine = create_async_engine(
        get_settings().database_url.get_secret_value(),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )
    yield engine
    await engine.dispose()


@asynccontextmanager
async def _books_held(engine: AsyncEngine, market_ids: Sequence[uuid.UUID]):
    """Every one of these books row-locked until the block ends."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        for market_id in market_ids:
            await conn.execute(
                text(
                    "SELECT 1 FROM ledger.market_books "
                    "WHERE market_id = :market_id FOR UPDATE"
                ),
                {"market_id": market_id},
            )
        try:
            yield
        finally:
            await transaction.rollback()


async def _waiting_on_a_book(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(BOOK_LOCK_WAITERS)).scalar_one()


async def _until_queued(
    engine: AsyncEngine, at_least: int, tasks: Sequence[asyncio.Task]
) -> None:
    """Wait until `at_least` backends queue on a book, then a little longer,
    so any request the limits fail to hold back has queued too."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_TIMEOUT
    while await _waiting_on_a_book(engine) < at_least:
        for task in tasks:
            if task.done():
                pytest.fail(f"a party finished while the books were held: {task}")
        if loop.time() > deadline:
            pytest.fail(f"timed out: {at_least} never queued on the held books")
        await asyncio.sleep(0.01)
    await asyncio.sleep(_SETTLE_SECONDS)


async def _is_ready() -> bool:
    """What `/ready` answers for the database, on the app's own engine."""
    answers = await readiness_of({"database": lambda: is_database_reachable(get_engine())})
    return answers["database"]


async def _parties_on(
    markets: Sequence[Upstream], per_market: int, submit: Submit
) -> list[asyncio.Task]:
    """`per_market` funded traders on each market, started together at a
    barrier, none connected before they submit."""
    factory = session_factory()
    users = [uuid.uuid4() for _ in range(len(markets) * per_market)]
    async with factory() as setup:
        for user_id in users:
            await fund(setup, user_id)
    start = asyncio.Barrier(len(users))

    async def party(upstream: Upstream, user_id: uuid.UUID):
        async with factory() as own:
            await start.wait()
            return await submit(own, upstream, user_id, f"slot-{user_id}")

    return [
        asyncio.create_task(party(markets[i % len(markets)], user_id))
        for i, user_id in enumerate(users)
    ]


async def _warm_markets(count: int) -> list[Upstream]:
    markets = [Upstream() for _ in range(count)]
    async with session_factory()() as setup:
        for upstream in markets:
            await warm(setup, upstream)
    return markets


async def _finish(tasks: Sequence[asyncio.Task]) -> list[object]:
    async with asyncio.timeout(TIMEOUT):
        return await asyncio.gather(*tasks)


@pytest.mark.parametrize("submit", [_trade, _placement], ids=["trade", "placement"])
async def test_on_one_hot_market_only_the_limit_hold_a_connection(
    production_pool, outside_engine: AsyncEngine, submit: Submit
) -> None:
    """Eight requests, as many as the whole pool, queue on one held book. Two
    hold a connection, the readiness check gets one at once, and once the
    book is free every request commits and the ledger still sums to zero.

    Remove the market's limit and six requests take connections (the
    process-wide limit still saves `/ready`). Remove the slot from
    `trading.execute` or `orders.place`, or both limits, and all eight do,
    and the readiness check times out."""
    (upstream,) = await _warm_markets(1)
    parties = _POOL_SIZE + _MAX_OVERFLOW

    async with _books_held(outside_engine, [upstream.market_id]):
        tasks = await _parties_on([upstream], parties, submit)
        await _until_queued(outside_engine, _PER_MARKET, tasks)

        holding = get_engine().pool.checkedout()
        ready = await _is_ready()

    results = await _finish(tasks)
    assert holding <= _PER_MARKET, (
        f"{holding} requests on one market held a connection; the limit is {_PER_MARKET}"
    )
    assert ready, "the readiness check found no connection while trades queued"

    assert len(results) == parties
    async with session_factory()() as check:
        if submit is _trade:
            assert (await book_row(check, upstream.market_id)).state_version == parties
        else:
            assert await order_count(check) == parties
        await assert_ledger_balances(check)


async def test_across_hot_markets_the_reserve_stays_free_for_reads(
    production_pool, outside_engine: AsyncEngine
) -> None:
    """Four hot markets at two each would be eight, the whole pool. The
    process-wide limit holds them to six, so two stay free and the readiness
    check gets one at once; then every trade commits.

    Remove the process-wide limit and all eight hold a connection, and the
    readiness check times out behind them."""
    markets = await _warm_markets(4)
    per_market = 3

    async with _books_held(outside_engine, [m.market_id for m in markets]):
        tasks = await _parties_on(markets, per_market, _trade)
        await _until_queued(outside_engine, _TRADE_CONNECTIONS, tasks)

        holding = get_engine().pool.checkedout()
        ready = await _is_ready()

    await _finish(tasks)
    assert ready, (
        f"the readiness check found no connection while {holding} trades held one"
    )
    assert holding <= _TRADE_CONNECTIONS, (
        f"{holding} trades held a connection; trades may hold {_TRADE_CONNECTIONS}"
    )

    async with session_factory()() as check:
        for upstream in markets:
            assert (await book_row(check, upstream.market_id)).state_version == per_market
        await assert_ledger_balances(check)


@pytest.mark.parametrize("outcome", ["replay", "refusal"])
async def test_a_trade_gives_its_connection_back_before_its_slot(
    session: AsyncSession, outcome: str
) -> None:
    """A replay commits nothing and a refusal raises, and either would keep
    its connection until the request's session closed, after the next trade
    had taken the slot. The slot ends the transaction first, so the limits
    count connections exactly. Remove that rollback and this fails."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await buy(session, upstream, user_id=user_id, client_key="first", state_version=0)

    if outcome == "replay":
        await buy(session, upstream, user_id=user_id, client_key="first", state_version=0)
    else:
        with pytest.raises(errors().QuoteStale):
            await buy(session, upstream, user_id=user_id, client_key="stale", state_version=0)

    assert not session.in_transaction()
    assert get_engine().pool.checkedout() == 0


class _StalledBus(Recorder):
    """A Redis whose every publish waits until `release` is set, as a stalled
    one does up to its socket timeout. `reached` counts the publishes that
    have started."""

    def __init__(self, release: asyncio.Event) -> None:
        super().__init__()
        self.reached = 0

        async def stall() -> None:
            self.reached += 1
            await release.wait()

        self.on_publish = stall


async def _until_publishing(bus: _StalledBus, task: asyncio.Task) -> None:
    async with asyncio.timeout(PROBE_TIMEOUT):
        while bus.reached == 0:
            if task.done():
                task.result()
                pytest.fail("the trade finished without publishing")
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("submit", ["trade", "placement"])
async def test_a_stalled_publish_does_not_hold_the_market_s_slot(
    clean_database, submit: str
) -> None:
    """ADR 0010: the publish never gets in a trade's way. Two requests on one
    market commit and then stall in their publish, which fills the market's
    two slots if the publish runs inside them. A third request must still
    commit while both publishes wait. Move the publish back inside the slot
    and the third waits for a slot until this gives up."""
    (upstream,) = await _warm_markets(1)
    users = [uuid.uuid4() for _ in range(3)]
    async with session_factory()() as setup:
        for user_id in users:
            await fund(setup, user_id)
    release = asyncio.Event()

    async def commit_one(user_id: uuid.UUID, redis_client: Recorder):
        async with session_factory()() as own:
            if submit == "trade":
                return await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    quantity=SMALL_QUANTITY,
                    state_version=None,
                    limit=_BUY_LIMIT,
                    client_key=f"stall-{user_id}",
                    redis_client=redis_client,
                )
            # At the market's price, so the placement fills and publishes.
            return await place(
                own,
                upstream,
                user_id=user_id,
                limit_price=Decimal("0.9900"),
                quantity=SMALL_QUANTITY,
                redis_client=redis_client,
            )

    stalled = []
    for user_id in users[:2]:
        bus = _StalledBus(release)
        task = asyncio.create_task(commit_one(user_id, bus))
        await _until_publishing(bus, task)
        stalled.append(task)

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        async with asyncio.timeout(_STALLED_PUBLISH_PATIENCE):
            await commit_one(users[2], Recorder())
    except TimeoutError:
        pytest.fail(
            "a third request waited for a slot while two publishes stalled: "
            "the publish runs inside the market's slot"
        )
    finally:
        release.set()
        await _finish(stalled)
    print(f"third request committed in {loop.time() - started:.3f} s")

    async with session_factory()() as check:
        assert (await book_row(check, upstream.market_id)).state_version == 3
        await assert_ledger_balances(check)
