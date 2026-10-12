"""A market's price history, without HTTP. [BE][2.4] #63

What the service owns: the market's trades read from its own pool feed and
replayed, the opening point, the result beside the points, the wiring of
`start` and `end` into the window, and a replay refusal as a domain error.
DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it", "Settlement adds no point to the price history; the
series ends at the last trade" and "The price-history replay refuses trades
whose state versions do not run 1, 2, 3, … in order". The window's own rules
are `core/test_price_history.py`'s; the wire shape and the guard are
`controller/test_price_history_routes.py`'s.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import PriceHistoryInconsistent
from service.price_history import price_history_of_market
from unit_test.price_history_fixtures import (
    RAW_PRICES,
    WIRE_PRICES,
    entry_times,
    gapped,
    opened_at_of,
    result_row_of,
    traded,
)
from unit_test.trade_feed_fixtures import feed, settled_feed
from unit_test.trade_fixtures import entities

_PLUS_EIGHT = timezone(timedelta(hours=8))
_HALF_A_QUANTUM = Decimal("0.00005")


def _versions(history) -> list[int]:
    return [point.state_version for point in history.points]


def _times(history) -> list[datetime]:
    return [point.occurred_at for point in history.points]


# =========================================================================
# The points
# =========================================================================
async def test_a_buy_of_50_at_b_100_prices_outcome_0_at_0_6225(
    session: AsyncSession,
) -> None:
    """The prices are `book_prices.priced`'s, as the snapshot's are: quantized
    by `quantize_price` to scale 4, `ROUND_HALF_UP`. So exact Decimals at
    exponent -4, each within half a quantum of the raw price worked by hand
    in `price_history_fixtures.py`. Catches: `seed_subsidy` (250) priced as
    `b`, which gives 0.5498; raw engine prices that skip `priced`."""
    scenario = await traded(session)

    history = await price_history_of_market(
        session, scenario.market_id, start=None, end=None
    )

    by_version = {point.state_version: point for point in history.points}
    for state_version in (1, 2, 3):
        prices = [priced.price for priced in by_version[state_version].prices]
        assert prices == [Decimal(price) for price in WIRE_PRICES[state_version]]
        for price, raw in zip(prices, RAW_PRICES[state_version], strict=True):
            assert price.as_tuple().exponent == -4, price
            assert abs(price - raw) < _HALF_A_QUANTUM, (price, raw)


async def test_the_opening_point_is_at_the_book_s_opened_at_at_even_prices(
    session: AsyncSession,
) -> None:
    """"The first point is the book's opening. `state_version` 0, every
    outcome at the same price, at the book's `opened_at`." On a traded book,
    where `state_changed_at` has moved on. Catches: the opening stamped with
    `state_changed_at`."""
    scenario = await traded(session)
    opened_at = await opened_at_of(session, scenario.market_id)

    history = await price_history_of_market(
        session, scenario.market_id, start=None, end=None
    )

    opening = history.points[0]
    assert opening.state_version == 0
    assert opening.occurred_at == opened_at
    assert [priced.price for priced in opening.prices] == [
        Decimal(price) for price in WIRE_PRICES[0]
    ]


async def test_start_and_end_reach_the_window(session: AsyncSession) -> None:
    """The window's rules are core's; this is the wiring. `start` is the
    midpoint of trades 1 and 2 in +08:00, so trade 1 is the carry-in, and
    `end` is trade 2's own time. Each returned point's prices are the full
    history's for the same `state_version`, the ticket's "same price as in the
    full history". Catches: the service dropping `start` (`[0, 1, 2]`) or
    dropping `end` (`[1, 2, 3]`); a windowed point priced from another
    point's `q`."""
    scenario = await traded(session)
    times = await entry_times(
        session, [scenario.a_buy.transaction_id, scenario.b_buy.transaction_id]
    )
    t1 = times[scenario.a_buy.transaction_id]
    t2 = times[scenario.b_buy.transaction_id]
    assert t2 - t1 > timedelta(microseconds=2), "the midpoint needs room"
    midpoint = (t1 + (t2 - t1) / 2).astimezone(_PLUS_EIGHT)

    history = await price_history_of_market(
        session, scenario.market_id, start=midpoint, end=t2
    )

    assert _versions(history) == [1, 2]
    full = await price_history_of_market(
        session, scenario.market_id, start=None, end=None
    )
    full_prices = {point.state_version: point.prices for point in full.points}
    for point in history.points:
        assert point.prices == full_prices[point.state_version], point.state_version


async def test_a_market_s_history_holds_only_its_own_trades(
    session: AsyncSession,
) -> None:
    """Two markets with trades, so a feed read that is not tied to this
    market's pool account shows. Catches: the trade statement filtered on the
    kinds and not on `account_id == book.pool_account_id`."""
    scenario = await feed(session)
    m1_trades = [scenario.a_buy, scenario.a_sell, scenario.b_buy]
    times = await entry_times(
        session,
        [trade.transaction_id for trade in (*m1_trades, scenario.m2_buy)],
    )

    m1 = await price_history_of_market(
        session, scenario.m1.market_id, start=None, end=None
    )
    m2 = await price_history_of_market(
        session, scenario.m2.market_id, start=None, end=None
    )

    assert _versions(m1) == [0, 1, 2, 3]
    assert _times(m1)[1:] == [times[trade.transaction_id] for trade in m1_trades]
    assert _versions(m2) == [0, 1]
    assert _times(m2)[1:] == [times[scenario.m2_buy.transaction_id]]


# =========================================================================
# The result
# =========================================================================
async def test_a_settled_market_reports_its_result_and_ends_at_its_last_trade(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "Settlement adds no point to the price history". The
    expected winner and time are `ledger.market_results`' own row, and the
    last trade is the loser's, so the last trade's outcome cannot pass for the
    winner. `result` is the record's `ResultKind`, as on the exposure read.
    Catches: a point appended at `recorded_at` for the result."""
    scenario = await settled_feed(session)
    market_id = scenario.upstream.market_id
    winner, recorded_at = await result_row_of(session, market_id)
    assert winner != scenario.loser_buy.outcome_id
    last_trade_at = (
        await entry_times(session, [scenario.loser_buy.transaction_id])
    )[scenario.loser_buy.transaction_id]

    history = await price_history_of_market(session, market_id, start=None, end=None)

    assert _versions(history) == [0, 1, 2]
    assert history.points[-1].occurred_at == last_trade_at
    assert history.result is entities().ResultKind.SETTLED
    assert history.winning_outcome_id == winner
    assert history.result_recorded_at == recorded_at


async def test_an_unsettled_market_reports_no_result_while_another_is_settled(
    session: AsyncSession,
) -> None:
    """A settled market sits in the same database, so a result read that is
    not tied to this market finds one. Catches: `select(MarketResult)` with
    no `market_id ==`."""
    await settled_feed(session)
    scenario = await traded(session)

    history = await price_history_of_market(
        session, scenario.market_id, start=None, end=None
    )

    assert history.result is None
    assert history.winning_outcome_id is None
    assert history.result_recorded_at is None


# =========================================================================
# A replay refusal
# =========================================================================
async def test_a_gap_in_the_trades_is_price_history_inconsistent(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "The price-history replay refuses trades whose state
    versions do not run 1, 2, 3, … in order". The pool feed runs 1, 3 (see
    `gapped`). Catches: the replay's `ValueError` escaping unmapped, or
    swallowed into a series labelled wrong."""
    upstream = await gapped(session)

    with pytest.raises(PriceHistoryInconsistent):
        await price_history_of_market(session, upstream.market_id, start=None, end=None)
