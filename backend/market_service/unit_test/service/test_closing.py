"""Automatic close at closing time, driven through the service layer. [F-4] #44.

The predicate, which is exact and decides trading, comes first; the sweep,
which only writes it down, second. If the sweep broke, the first section would
still pass, which is the property ADR 0011 buys.
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
from unit_test.conftest import (
    actor,
    approved_market,
    close_request,
    closed_market,
    overdue_market,
    proposal_request,
    proposed_market,
    published_market,
    rejection_request,
)

# Small enough to keep the suite quick, large enough that a sweeper ticking on
# it is not racing the event loop.
_TICK = 0.01


def _market_closing_in(
    closes_in: timedelta, *, status: MarketStatus = MarketStatus.OPEN
) -> Market:
    """A market in `status`, closing `closes_in` from now.

    Built directly, because `publish` refuses a market already past its close.
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
    """Re-read from the database; the sweep's bulk UPDATE bypasses the identity map."""
    session.expire_all()
    return (await session.execute(select(Market).where(Market.id == market_id))).scalar_one()


# --- the predicate, which is the authority --------------------------------
# No database and no sweeper: whether a trade may execute is a fact about two
# values.


def test_an_open_market_before_its_close_time_trades() -> None:
    assert closing.is_open_for_trading(
        _market_closing_in(timedelta(hours=1))
    )


def test_an_open_market_past_its_close_time_does_not_trade() -> None:
    """The reason this module exists: the status still says OPEN. ADR 0011."""
    market = _market_closing_in(timedelta(seconds=-1))

    assert market.status is MarketStatus.OPEN
    assert not closing.is_open_for_trading(market)


def test_the_close_time_itself_is_closed() -> None:
    """Exclusive: a market closing at noon does not trade at noon."""
    now = datetime.now(UTC)
    market = _market_closing_in(timedelta(0))
    market.close_time = now

    assert not closing.is_open_for_trading(market, now=now)


@pytest.mark.parametrize(
    "status", [MarketStatus.DRAFT, MarketStatus.SUBMITTED, MarketStatus.CLOSED]
)
def test_only_an_open_market_trades(status: MarketStatus) -> None:
    """The clock is half the rule; the other half is having been published."""
    assert not closing.is_open_for_trading(_market_closing_in(timedelta(hours=1), status=status))


def test_a_market_with_no_close_time_does_not_trade() -> None:
    """Unreachable through the API; closed rather than open forever, the safer failure."""
    market = _market_closing_in(timedelta(hours=1))
    market.close_time = None

    assert not closing.is_open_for_trading(market)


async def test_the_sql_predicate_agrees_with_the_python_one(session: AsyncSession) -> None:
    """Browse filters in SQL and a trade checks an entity; they must agree. D-024."""
    live = _market_closing_in(timedelta(hours=1))
    expired = _market_closing_in(timedelta(seconds=-1))
    await _store(session, live, expired)

    rows = (
        await session.execute(select(Market.id).where(closing.open_for_trading()))
    ).scalars().all()

    assert set(rows) == {live.id}
    assert closing.is_open_for_trading(live)
    assert not closing.is_open_for_trading(expired)


async def _closed_early(session: AsyncSession, creator, *, at: datetime) -> Market:
    """A market `creator` published, stopped by hand at `at` with its
    `close_time` still a month ahead. [2.3] #7, ADR 0014."""
    market_id = (await published_market(session, creator)).id
    return await market_service.close_early(
        session, actor(), market_id, close_request(), now=at
    )


async def test_the_paging_predicate_agrees_with_the_live_one_at_now(
    session: AsyncSession,
) -> None:
    """#104: at `as_of = now`, `was_open_for_trading_at` picks exactly what
    `open_for_trading` picks, so grouping by it changes no first page.

    Covers every status a trader can see (`PUBLIC_STATUSES`), including ADR
    0011's unswept gap and early closes that later gained a proposal or had one
    rejected: a rejection keeps `closed_at` (ADR 0016), which is what keeps the
    time-only answer right. Drafts and submitted markets legitimately disagree
    (the time-only rule reads them as trading) and `browse` filters them out
    first, so they are absent on purpose.
    """
    creator = actor()
    a_minute_ago = datetime.now(UTC) - timedelta(minutes=1)

    trading_id = (await published_market(session, creator)).id
    early_id = (await _closed_early(session, creator, at=a_minute_ago)).id

    early_pending = await _closed_early(session, creator, at=a_minute_ago)
    early_pending_id = (
        await market_service.propose_outcome(
            session, creator, early_pending.id,
            proposal_request(early_pending.outcomes[0].id),
        )
    ).id

    early_rejected = await _closed_early(session, creator, at=a_minute_ago)
    proposed = await market_service.propose_outcome(
        session, creator, early_rejected.id,
        proposal_request(early_rejected.outcomes[0].id),
    )
    rejected_id = (
        await market_service.reject_outcome(
            session, actor(), proposed.id, rejection_request(proposed.proposal_id)
        )
    ).id

    # Ids held as they are created; the sweep in these fixtures expires the
    # session. The unswept market comes last, or their sweeps would close it.
    swept_id = (await closed_market(session, creator)).id
    pending_id = (await proposed_market(session, creator)).id
    approved_id = (await approved_market(session, creator)).id
    unswept_id = (await overdue_market(session, creator)).id
    now = datetime.now(UTC)

    live = set(
        (
            await session.execute(
                select(Market.id).where(closing.open_for_trading(now))
            )
        ).scalars()
    )
    historical = set(
        (
            await session.execute(
                select(Market.id).where(closing.was_open_for_trading_at(now))
            )
        ).scalars()
    )

    assert live == {trading_id}
    assert historical == live
    assert {early_id, early_pending_id, rejected_id, unswept_id, swept_id,
            pending_id, approved_id}.isdisjoint(historical)


async def test_the_paging_predicate_ignores_a_status_written_after_as_of(
    session: AsyncSession,
) -> None:
    """#104: an early close between two page reads must not move the market out
    of the group the first page put it in, or it is listed twice.

    As of a minute before the admin stopped it, the market was still trading,
    though its status now reads CLOSED. ANDing `status == OPEN` in would fail
    this.
    """
    stopped_at = datetime.now(UTC)
    market_id = (await _closed_early(session, actor(), at=stopped_at)).id
    as_of = stopped_at - timedelta(minutes=1)

    ids = set(
        (
            await session.execute(
                select(Market.id).where(closing.was_open_for_trading_at(as_of))
            )
        ).scalars()
    )

    assert market_id in ids
    assert market_id not in set(
        (
            await session.execute(
                select(Market.id).where(closing.was_open_for_trading_at(stopped_at))
            )
        ).scalars()
    )


# --- the sweep, which materialises it --------------------------------------


async def test_a_due_market_is_closed(session: AsyncSession) -> None:
    """[F-4] #44's acceptance criterion: OPEN becomes CLOSED with no admin involved."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)

    closed = await closing.close_due_markets(session, limit=10)

    assert closed == [market.id]
    stored = await _reload(session, market.id)
    assert stored.status is MarketStatus.CLOSED
    assert stored.closed_at is not None
    # Aware, like every timestamp this service stores.
    assert stored.closed_at.tzinfo is not None


async def test_closing_is_committed(session: AsyncSession) -> None:
    """Read from a second session, so this fails if the sweep never commits."""
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
    """An unpublished market past its close time is incomplete, not closed, and
    must stay editable."""
    market = _market_closing_in(timedelta(seconds=-1), status=status)
    await _store(session, market)

    assert await closing.close_due_markets(session, limit=10) == []
    assert (await _reload(session, market.id)).status is status


async def test_sweeping_twice_closes_nothing_the_second_time(
    session: AsyncSession,
) -> None:
    """A caller may act on the returned ids, so each appears once."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)

    assert await closing.close_due_markets(session, limit=10) == [market.id]
    assert await closing.close_due_markets(session, limit=10) == []


async def test_the_batch_limit_is_a_ceiling(session: AsyncSession) -> None:
    """Bounds the row locks one transaction holds."""
    markets = [
        _market_closing_in(timedelta(seconds=-1)) for _ in range(3)
    ]
    await _store(session, *markets)

    assert len(await closing.close_due_markets(session, limit=2)) == 2
    assert len(await closing.close_due_markets(session, limit=2)) == 1
    assert await closing.close_due_markets(session, limit=2) == []


async def test_closing_touches_the_market(session: AsyncSession) -> None:
    """`updated_at` moves, lifting the market up its creator's list. It comes from
    `onupdate` firing on a bulk UPDATE, which is easy to lose by accident."""
    market = _market_closing_in(timedelta(seconds=-1))
    await _store(session, market)
    before = market.updated_at

    await closing.close_due_markets(session, limit=10)

    assert (await _reload(session, market.id)).updated_at > before


async def test_the_oldest_due_market_closes_first(session: AsyncSession) -> None:
    """A backlog drains in the order markets came due."""
    oldest = _market_closing_in(timedelta(hours=-3))
    newest = _market_closing_in(timedelta(seconds=-1))
    await _store(session, newest, oldest)

    assert await closing.close_due_markets(session, limit=1) == [oldest.id]


async def test_two_sweepers_close_each_market_exactly_once(
    clean_database: None,
) -> None:
    """Two uncoordinated replicas close, and return, each market once. ADR 0011."""
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
    """A form left open keeps autosaving after the market closes."""
    admin = actor()
    market = _market_closing_in(timedelta(seconds=-1))
    market.creator_id = admin.id
    await _store(session, market)
    await closing.close_due_markets(session, limit=10)
    # Reloads past the stale identity map; a real request has its own session.
    assert (await _reload(session, market.id)).status is MarketStatus.CLOSED

    with pytest.raises(MarketClosed):
        await market_service.save(session, admin, _draft_request(market.draft_key))


async def test_publishing_a_closed_market_is_refused(session: AsyncSession) -> None:
    """Not `market_not_submitted`: nothing the admin does can publish this market."""
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
    """Back-to-back batches, not one per tick, so an outage catches up at once."""
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
    """The error policy: a transient failure is a logged blip, never a dead task. ADR 0011."""
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
    """The broad handler must let `CancelledError` through; the timeout turns a hang
    into a failure."""
    task = asyncio.create_task(
        run_close_sweeper(get_session_factory(), interval_seconds=60, batch_limit=5)
    )
    await asyncio.sleep(_TICK)
    task.cancel()

    async with asyncio.timeout(5):
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert task.cancelled()
