"""The ledger's business metrics: each counts its event once, after the commit.
#599.

Driven through the real service functions with the suites' own fixtures. The
registry is the process-wide one, so every assertion is a difference between
a reading before the call and one after it, never an absolute value.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import redis.exceptions
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import InsufficientFunds
from unit_test.order_fixtures import add_order
from unit_test.placement_fixtures import limit_upstream, open_at, place
from unit_test.settlement_fixtures import WINNER, SettleUpstream, hold, settle
from unit_test.settlement_fixtures import warm as warm_settlement
from unit_test.trade_fixtures import Recorder, Upstream, buy, fund, unaffordable, warm
from unit_test.void_fixtures import VoidUpstream, void

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))


# Every series a test below asserts on, as `(name, labels)`.
_SERIES = [
    ("ledger_trades_committed_total", {"side": "buy"}),
    ("ledger_trades_committed_total", {"side": "sell"}),
    ("ledger_book_lock_wait_seconds_count", {}),
    ("ledger_order_fills_total", {}),
    ("ledger_settlements_total", {}),
    ("ledger_voids_total", {}),
    ("ledger_publish_failures_total", {"channel": "market.price"}),
    ("ledger_publish_failures_total", {"channel": "order.update"}),
]


def _reading(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _key_of(name: str, labels: dict[str, str]) -> tuple:
    return (name, tuple(sorted(labels.items())))


class _Readings:
    """Every series in `_SERIES`, read when this is made, so `delta` is what
    happened since."""

    def __init__(self) -> None:
        self._before = {
            _key_of(name, labels): _reading(name, labels) for name, labels in _SERIES
        }

    def delta(self, name: str, **labels: str) -> float:
        return _reading(name, labels) - self._before[_key_of(name, labels)]


async def _funded(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    await fund(session, user_id)
    return user_id


def test_every_series_exists_before_anything_happens() -> None:
    """A series created on first use is missing from a dashboard until the
    first event, and a `rate()` over it reads as no data rather than zero."""
    import core.metrics  # noqa: F401, PLC0415

    for name, labels in _SERIES:
        assert REGISTRY.get_sample_value(name, labels) is not None, (name, labels)


# =========================================================================
# Trades
# =========================================================================
async def test_a_committed_trade_is_counted_once(session: AsyncSession) -> None:
    """#599's Definition of Done: a trade increments the trades counter. One
    trade takes the book lock once, and the wait for it is timed once."""
    upstream = Upstream()
    await warm(session, upstream)
    user_id = await _funded(session)
    readings = _Readings()

    await buy(session, upstream, user_id=user_id)

    assert readings.delta("ledger_trades_committed_total", side="buy") == 1
    assert readings.delta("ledger_trades_committed_total", side="sell") == 0
    assert readings.delta("ledger_book_lock_wait_seconds_count") == 1


async def test_a_refused_trade_is_not_counted(session: AsyncSession) -> None:
    """#599's Definition of Done: a rolled-back trade does not increment it.
    The refusal is raised by the commit itself, so a counter placed before
    `post_all` would count it."""
    upstream = Upstream()
    await warm(session, upstream)
    user_id = uuid.uuid4()
    credits = await fund(session, user_id)
    readings = _Readings()

    with pytest.raises(InsufficientFunds):
        await buy(session, upstream, user_id=user_id, quantity=unaffordable(credits))
    await session.rollback()

    assert readings.delta("ledger_trades_committed_total", side="buy") == 0


async def test_a_replayed_trade_is_not_counted_again(session: AsyncSession) -> None:
    """A retry with the same key answers the first trade and commits nothing,
    so it is not a second trade."""
    upstream = Upstream()
    await warm(session, upstream)
    user_id = await _funded(session)
    readings = _Readings()

    await buy(session, upstream, user_id=user_id, client_key="once")
    await buy(session, upstream, user_id=user_id, client_key="once")

    assert readings.delta("ledger_trades_committed_total", side="buy") == 1


async def test_a_failed_price_publish_is_counted_on_its_channel(
    session: AsyncSession,
) -> None:
    """ADR 0010: the trade has committed and stands, and the swallowed failure
    is counted on the channel it failed on."""
    upstream = Upstream()
    await warm(session, upstream)
    user_id = await _funded(session)
    readings = _Readings()

    await buy(
        session,
        upstream,
        user_id=user_id,
        redis_client=Recorder(raises=redis.exceptions.ConnectionError("down")),
    )

    assert readings.delta("ledger_publish_failures_total", channel="market.price") == 1
    assert readings.delta("ledger_publish_failures_total", channel="order.update") == 0
    assert readings.delta("ledger_trades_committed_total", side="buy") == 1


# =========================================================================
# Limit-order fills
# =========================================================================
async def test_each_resting_order_a_trade_fills_is_counted(
    session: AsyncSession,
) -> None:
    """A trade that fills one resting order counts one fill, beside the trade.
    `test_cascade.py`'s flat market: a buy of 10 outcome-1 shares at 0.4500
    rests, and a buy of 90 outcome-0 shares fills it whole."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    owner, trader = await _funded(session), await _funded(session)
    await place(
        session,
        upstream,
        user_id=owner,
        outcome=1,
        limit_price=Decimal("0.4500"),
        quantity=Decimal("10.0000"),
    )
    readings = _Readings()

    await buy(session, upstream, user_id=trader, quantity=Decimal("90.0000"))

    assert readings.delta("ledger_order_fills_total") == 1
    assert readings.delta("ledger_trades_committed_total", side="buy") == 1


async def test_a_placement_that_fills_counts_its_own_fill_and_its_cascade_but_no_trade(
    session: AsyncSession,
) -> None:
    """`test_place_order.py`'s broken book: a buy of 10 outcome-1 shares at
    0.4700 fills on arrival, and its cascade fills a share left resting while
    the price made it executable. Two fills; a placement is not a trade."""
    upstream = limit_upstream()
    await open_at(session, upstream)
    owner, placer = await _funded(session), await _funded(session)
    await add_order(
        session,
        user_id=owner,
        market_id=upstream.market_id,
        outcome_id=upstream.outcomes[1],
        budget="0.5000",
    )
    await session.commit()
    readings = _Readings()

    placement = await place(
        session,
        upstream,
        user_id=placer,
        outcome=1,
        limit_price=Decimal("0.4700"),
        quantity=Decimal("10.0000"),
    )

    assert placement.book_state_version == 2, "the cascade did not fill"
    assert readings.delta("ledger_order_fills_total") == 2
    assert readings.delta("ledger_trades_committed_total", side="buy") == 0


# =========================================================================
# Settlements and voids
# =========================================================================
async def test_a_settlement_is_counted_once_and_its_replay_is_not(
    session: AsyncSession,
) -> None:
    upstream = SettleUpstream()
    await warm_settlement(session, upstream)
    await hold(session, upstream, [(uuid.uuid4(), WINNER, Decimal("5.0000"))])
    readings = _Readings()

    await settle(session, upstream)
    await settle(session, upstream)

    assert readings.delta("ledger_settlements_total") == 1


async def test_a_void_is_counted_once_and_its_replay_is_not(
    session: AsyncSession,
) -> None:
    upstream = VoidUpstream()
    upstream.closes()
    readings = _Readings()

    await void(session, upstream)
    await void(session, upstream)

    assert readings.delta("ledger_voids_total") == 1
