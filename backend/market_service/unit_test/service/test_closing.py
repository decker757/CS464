"""Automatic close at closing time, driven through the service layer. [F-4] #44.

Two things are under test here and they are not the same thing, which is the
point of the ticket:

- **the predicate**, which decides whether a trade may execute and is exact
- **the sweep**, which writes that decision into `status` a few seconds later

The tests are ordered that way deliberately. If the sweep broke entirely, every
test in the first section would still pass and no trade would execute against a
closed market — that is the property the design is buying, and it is worth
being able to see it fail on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import MarketClosed
from model.entities import Market, MarketStatus
from model.schemas import MarketDraftRequest
from service import closing, market_service, sweeper
from service.sweeper import run_close_sweeper
from unit_test.conftest import actor

# Small enough to keep the suite quick, large enough that a sweeper ticking on
# it is not racing the event loop.
_TICK = 0.01


def _market_closing_in(
    closes_in: timedelta, *, status: MarketStatus = MarketStatus.OPEN
) -> Market:
    """A market in `status`, closing `closes_in` from now.

    Named for what makes it different from `test_validation.py`'s `_market`,
    which builds a market against a frozen clock to break one rule at a time.
    This one is positioned relative to its own closing time, because that
    relationship is the entire subject of this file.

    Built directly rather than through `save` and `publish`, because the thing
    most of these tests need is a market that is already past its closing time
    and `publish` refuses to create one: it re-runs the rule that `close_time`
    must be in the future, which `test_publishing.py` asserts and this ticket
    relies on. Going through the real path would mean publishing and then
    rewriting the column anyway.
    """
    now = datetime.now(UTC)
    return Market(
        creator_id=uuid.uuid4(),
        draft_key=uuid.uuid4(),
        status=status,
        question="Will Singapore core inflation be below 2% in December 2026?",
        close_time=now + closes_in,
        resolution_time=now + closes_in + timedelta(days=15),
        published_at=now if status is MarketStatus.OPEN else None,
        liquidity_b=Decimal("100"),
        seed_subsidy=Decimal("250"),
    )


async def _store(session: AsyncSession, *markets: Market) -> None:
    session.add_all(markets)
    await session.commit()


async def _reload(session: AsyncSession, market_id: uuid.UUID) -> Market:
    """Re-read from the database.

    `close_due_markets` issues a bulk UPDATE with `synchronize_session=False`,
    so nothing in the identity map is refreshed by it. A test that asserted on
    the in-memory object would be asserting on a stale copy and would pass
    whether or not the sweep committed anything.
    """
    session.expire_all()
    return (await session.execute(select(Market).where(Market.id == market_id))).scalar_one()


# --- the predicate, which is the authority --------------------------------
# None of these touch the database or the sweeper. That is the whole claim:
# whether a trade may execute is a fact about two values, and no background job
# has to have run for it to be true.


def test_an_open_market_before_its_close_time_trades() -> None:
    assert closing.is_open_for_trading(
        _market_closing_in(timedelta(hours=1))
    )


def test_an_open_market_past_its_close_time_does_not_trade() -> None:
    """The reason this module exists.

    The status still says OPEN — no sweep has run and none needs to. A trade
    path that read `status` instead of calling this would execute here, which
    is exactly the window [F-4] #44 would otherwise have left open between the
    closing time and the next tick of a job.
    """
    market = _market_closing_in(timedelta(seconds=-1))

    assert market.status is MarketStatus.OPEN
    assert not closing.is_open_for_trading(market)


def test_the_close_time_itself_is_closed() -> None:
    """Exclusive, not inclusive. A market that closes at noon does not accept a
    trade at noon: `close_time` is when trading has stopped, not the last
    instant it is allowed."""
    now = datetime.now(UTC)
    market = _market_closing_in(timedelta(0))
    market.close_time = now

    assert not closing.is_open_for_trading(market, now=now)


@pytest.mark.parametrize(
    "status", [MarketStatus.DRAFT, MarketStatus.SUBMITTED, MarketStatus.CLOSED]
)
def test_only_an_open_market_trades(status: MarketStatus) -> None:
    """The clock is half the rule. The other half is an administrator having
    decided this market should be tradeable at all, and a draft with a future
    close time has never been that."""
    assert not closing.is_open_for_trading(_market_closing_in(timedelta(hours=1), status=status))


def test_a_market_with_no_close_time_does_not_trade() -> None:
    """Unreachable through the API, and closed rather than open forever if it
    ever happens: a market with no closing time has no resolution date either,
    and letting people trade into that is the worse of the two failures."""
    market = _market_closing_in(timedelta(hours=1))
    market.close_time = None

    assert not closing.is_open_for_trading(market)


async def test_the_sql_predicate_agrees_with_the_python_one(session: AsyncSession) -> None:
    """[BE][X] #62 and [2.1] #5 filter in SQL; [T-2] #22 checks an entity it has
    already locked. A market listed as tradeable and then refusing the trade is
    a bug report, so the two have to answer identically without the sweeper
    having run."""
    live = _market_closing_in(timedelta(hours=1))
    expired = _market_closing_in(timedelta(seconds=-1))
    await _store(session, live, expired)

    rows = (
        await session.execute(select(Market.id).where(closing.open_for_trading()))
    ).scalars().all()

    assert set(rows) == {live.id}
    assert closing.is_open_for_trading(live)
    assert not closing.is_open_for_trading(expired)


# --- the sweep, which materialises it --------------------------------------


async def test_a_due_market_is_closed(session: AsyncSession) -> None:
    """[F-4] #44's whole acceptance criterion: OPEN becomes CLOSED once the
    closing time has passed, with no admin involved."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)

    closed = await closing.close_due_markets(session, limit=10)

    assert closed == [market.id]
    stored = await _reload(session, market.id)
    assert stored.status is MarketStatus.CLOSED
    assert stored.closed_at is not None
    # Aware, like every other timestamp this service stores. CLAUDE.md records
    # that naive-versus-aware has already caused bugs here.
    assert stored.closed_at.tzinfo is not None


async def test_closing_is_committed(session: AsyncSession) -> None:
    """Asserted after expiring the identity map, so this fails if the sweep
    only ever changed an in-memory object."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)

    await closing.close_due_markets(session, limit=10)

    async with get_session_factory()() as other:
        stored = (
            await other.execute(select(Market).where(Market.id == market.id))
        ).scalar_one()
        assert stored.status is MarketStatus.CLOSED


async def test_a_market_that_is_not_due_is_left_alone(session: AsyncSession) -> None:
    market = _market_closing_in(timedelta(hours=1))
    await _store(session, market)

    assert await closing.close_due_markets(session, limit=10) == []
    assert (await _reload(session, market.id)).status is MarketStatus.OPEN


@pytest.mark.parametrize("status", [MarketStatus.DRAFT, MarketStatus.SUBMITTED])
async def test_only_open_markets_are_swept(
    session: AsyncSession, status: MarketStatus
) -> None:
    """A draft whose close time drifted into the past is an incomplete market,
    not a closed one. It is still editable, and its own submission rules will
    tell the admin to fix the date. Sweeping it would take away the one status
    from which that is possible."""
    market = _market_closing_in(timedelta(seconds=-1), status=status)
    await _store(session, market)

    assert await closing.close_due_markets(session, limit=10) == []
    assert (await _reload(session, market.id)).status is status


async def test_sweeping_twice_closes_nothing_the_second_time(
    session: AsyncSession,
) -> None:
    """The returned ids are what a caller may act on — an audit entry, a
    broadcast — so a market appearing in two sweeps would mean doing that twice
    for one closing."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)

    assert await closing.close_due_markets(session, limit=10) == [market.id]
    assert await closing.close_due_markets(session, limit=10) == []


async def test_the_batch_limit_is_a_ceiling(session: AsyncSession) -> None:
    """Bounds the transaction and the row locks, so a backlog is worked through
    in chunks rather than in one long transaction against live traffic."""
    markets = [
        _market_closing_in(timedelta(seconds=-1)) for _ in range(3)
    ]
    await _store(session, *markets)

    assert len(await closing.close_due_markets(session, limit=2)) == 2
    assert len(await closing.close_due_markets(session, limit=2)) == 1
    assert await closing.close_due_markets(session, limit=2) == []


async def test_closing_touches_the_market(session: AsyncSession) -> None:
    """`updated_at` moves, so a market that just closed rises to the top of its
    creator's list — which `list_for_creator` orders by that column, and which
    is where the administrator who now has to propose an outcome should find
    it. Asserted rather than assumed: it falls out of the column's `onupdate`
    firing on a bulk UPDATE, which is easy to lose by accident."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)
    before = market.updated_at

    await closing.close_due_markets(session, limit=10)

    assert (await _reload(session, market.id)).updated_at > before


async def test_the_oldest_due_market_closes_first(session: AsyncSession) -> None:
    """`ORDER BY close_time` under a backlog, so a sweeper drains in the order
    markets actually came due rather than in index order."""
    oldest = _market_closing_in(timedelta(hours=-3))
    newest = _market_closing_in(timedelta(seconds=-1))
    await _store(session, newest, oldest)

    assert await closing.close_due_markets(session, limit=1) == [oldest.id]


async def test_two_sweepers_close_each_market_exactly_once(
    clean_database: None,
) -> None:
    """Two replicas both run this loop and coordinate about nothing.

    `SELECT ... FOR UPDATE SKIP LOCKED` hands them disjoint rows and the outer
    UPDATE re-checks the status regardless, so every market is closed once and
    appears in exactly one caller's return value. That is what makes it safe
    for a caller to act on the list it gets back.
    """
    factory = get_session_factory()
    markets = [
        _market_closing_in(timedelta(seconds=-1)) for _ in range(8)
    ]
    async with factory() as setup:
        await _store(setup, *markets)

    async def sweep() -> list[uuid.UUID]:
        async with factory() as s:
            return await closing.close_due_markets(s, limit=8)

    first, second = await asyncio.gather(sweep(), sweep())

    assert set(first) & set(second) == set()
    assert set(first) | set(second) == {m.id for m in markets}


# --- the frozen market -----------------------------------------------------


def _draft_request(draft_key: uuid.UUID) -> MarketDraftRequest:
    return MarketDraftRequest(draft_key=draft_key, question="Edited after closing")


async def test_an_autosave_to_a_closed_market_is_refused(session: AsyncSession) -> None:
    """The create form may still be open behind the publish button, ticking
    every three seconds, when the closing time arrives. It must not be able to
    rewrite terms that traders finished pricing against and [3.1] #9 is about
    to settle on."""
    admin = actor()
    market = _market_closing_in(timedelta(seconds=-1))
    market.creator_id = admin.id
    await _store(session, market)
    await closing.close_due_markets(session, limit=10)
    # Expires the identity map, which the sweep's bulk UPDATE does not touch.
    # A request would use its own session and never see the stale copy; sharing
    # one here is the test's shortcut, not the service's behaviour.
    assert (await _reload(session, market.id)).status is MarketStatus.CLOSED

    with pytest.raises(MarketClosed):
        await market_service.save(session, admin, _draft_request(market.draft_key))


async def test_publishing_a_closed_market_is_refused(session: AsyncSession) -> None:
    """Not `market_not_submitted`, which would tell the admin to press submit —
    there is no sequence of actions that publishes this market, because
    publishing re-runs the rule that a close time must be in the future."""
    admin = actor()
    market = _market_closing_in(timedelta(seconds=-1))
    market.creator_id = admin.id
    await _store(session, market)
    await closing.close_due_markets(session, limit=10)
    assert (await _reload(session, market.id)).status is MarketStatus.CLOSED

    with pytest.raises(MarketClosed):
        await market_service.publish(session, admin, market.id)


# --- the loop --------------------------------------------------------------


async def test_a_backlog_is_drained_in_one_pass(clean_database: None) -> None:
    """Back-to-back batches rather than one per tick, so a service that was down
    catches up in seconds instead of in `interval * markets` seconds."""
    factory = get_session_factory()
    async with factory() as setup:
        await _store(
            setup,
            *[
                _market_closing_in(timedelta(seconds=-1))
                for _ in range(5)
            ],
        )

    assert await sweeper._drain(factory, 2) == 5

    async with factory() as check:
        remaining = (
            await check.execute(
                select(Market.id).where(Market.status == MarketStatus.OPEN)
            )
        ).scalars().all()
    assert remaining == []


async def test_the_sweeper_survives_a_failing_sweep(
    clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error policy, and the only reason the loop catches anything at all.

    A task that ended on a dropped connection is a service in which no market
    ever closes again and nothing says so. A transient failure has to be a
    logged blip and nothing more.
    """
    calls: list[int] = []

    async def flaky(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
        calls.append(limit)
        if len(calls) == 1:
            raise RuntimeError("the database went away")
        return []

    monkeypatch.setattr(closing, "close_due_markets", flaky)

    task = asyncio.create_task(
        run_close_sweeper(get_session_factory(), interval_seconds=_TICK, batch_limit=7)
    )
    try:
        async with asyncio.timeout(5):
            while len(calls) < 3:
                await asyncio.sleep(_TICK)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls[:3] == [7, 7, 7]


async def test_the_sweeper_stops_when_cancelled(clean_database: None) -> None:
    """`asyncio.CancelledError` derives from BaseException, so the broad handler
    in the loop lets a shutdown through untouched. If it ever caught it, this
    hangs rather than failing, which is why the timeout is here."""
    task = asyncio.create_task(
        run_close_sweeper(get_session_factory(), interval_seconds=60, batch_limit=5)
    )
    await asyncio.sleep(_TICK)
    task.cancel()

    async with asyncio.timeout(5):
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert task.cancelled()
