"""Each card's prices a week ago, carried by the batch snapshot. [X-1] #446.

Business rules of `service/snapshot.py::list_snapshots`'s `prices_7d_ago`:
the price after the last trade at or before `now` minus seven days, or None
when the market has no trade that old. DECISIONS.md, "A card's price change is
measured from the last trade at or before seven days ago, and a market with
none has no change". The wire shape is `test_snapshot_batch_routes.py`'s.

Trades are real ones through `trading.execute`, stamped by the clock as they
run. Time is pinned by handing the read a `now` worked out from the stored
stamps, read here from `ledger.entries`, never from the read under test.

Expected prices are literals: `WIRE_PRICES` are worked by hand in
`price_history_fixtures.py`, and the b = 137 pair below with `Decimal.exp` at
40 digits, rounded half up, so neither shares code with `core/lmsr.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.book_fixtures import capture_sql, mentioning, warm
from unit_test.conftest import drop_outcome_row
from unit_test.price_history_fixtures import (
    WIRE_PRICES,
    entry_times,
    gapped,
    opened_at_of,
    traded,
)
from unit_test.sell_fixtures import hold, open_at_zero
from unit_test.trade_fixtures import Upstream

# The documented window, as a literal: docs/api/ledger-service.md promises
# seven days.
_WINDOW = timedelta(days=7)
_ONE_MICROSECOND = timedelta(microseconds=1)


def _snapshot_service():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import snapshot  # noqa: PLC0415

    return snapshot


def _a_day_past_the_window() -> datetime:
    """A `now` whose window starts a day after every trade made so far."""
    return datetime.now(UTC) + _WINDOW + timedelta(days=1)


def _priced(prices) -> list[tuple[uuid.UUID, int, Decimal]]:  # noqa: ANN001
    return [(p.outcome_id, p.position, p.price) for p in prices]


def _wire(market_outcomes: list[uuid.UUID], state_version: int):
    return [
        (outcome_id, position, Decimal(price))
        for position, (outcome_id, price) in enumerate(
            zip(market_outcomes, WIRE_PRICES[state_version], strict=True)
        )
    ]


# =========================================================================
# The baseline
# =========================================================================
async def test_a_market_whose_one_trade_is_older_than_the_window_carries_its_price(
    session: AsyncSession,
) -> None:
    """DoD: a market with one trade, and trades older than the window. The
    baseline is real, the price that trade left, which is still the market's
    price: the card shows no change rather than no delta.

    b = 137, q = [50, 0]: p0 = 1 / (1 + e^(-50/137)) = 0.590241...
    """
    market = Upstream()
    await open_at_zero(session, market)
    trade = await hold(session, market, user_id=uuid.uuid4(), quantity=Decimal("50"))
    traded_at = (await entry_times(session, [trade.transaction_id]))[
        trade.transaction_id
    ]

    [listed] = await _snapshot_service().list_snapshots(
        session, [market.market_id], now=traded_at + _WINDOW + timedelta(hours=1)
    )

    assert _priced(listed.prices_7d_ago) == [
        (market.outcomes[0], 0, Decimal("0.5902")),
        (market.outcomes[1], 1, Decimal("0.4098")),
    ]


async def test_a_trade_exactly_at_the_window_start_is_in_the_baseline_and_a_later_one_is_not(
    session: AsyncSession,
) -> None:
    """"At or before": with the window starting at the second trade's own
    stamp, the baseline is the price that trade left (version 2), not the one
    before it (version 1) and not the third trade's (version 3)."""
    scenario = await traded(session)
    times = await entry_times(session, [t.transaction_id for t in scenario.trades])
    window_start = times[scenario.b_buy.transaction_id]

    [listed] = await _snapshot_service().list_snapshots(
        session, [scenario.market_id], now=window_start + _WINDOW
    )

    assert _priced(listed.prices_7d_ago) == _wire(scenario.upstream.outcomes, 2)


async def test_a_market_with_no_trade_at_or_before_the_window_start_has_no_baseline(
    session: AsyncSession,
) -> None:
    """AC: no history, or less history than the window, is no delta, never a
    change measured from the seed price. One market was never traded; the
    other was open at the window's start, at its seed price, and traded only
    a microsecond after it."""
    never_traded = Upstream()
    await open_at_zero(session, never_traded)
    scenario = await traded(session)
    times = await entry_times(session, [scenario.a_buy.transaction_id])
    window_start = times[scenario.a_buy.transaction_id] - _ONE_MICROSECOND
    assert await opened_at_of(session, scenario.market_id) <= window_start

    result = await _snapshot_service().list_snapshots(
        session,
        [never_traded.market_id, scenario.market_id],
        now=window_start + _WINDOW,
    )

    assert [(listed.market_id, listed.prices_7d_ago) for listed in result] == [
        (never_traded.market_id, None),
        (scenario.market_id, None),
    ]


async def test_a_feed_that_skips_a_state_version_has_no_baseline_and_keeps_its_price(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "The prices a week ago are replayed from each pool's
    trades up to the window's start, in one statement for every market": a
    feed the replay refuses costs that card its delta only, not the whole list
    its prices."""
    market = await gapped(session)

    [listed] = await _snapshot_service().list_snapshots(
        session, [market.market_id], now=_a_day_past_the_window()
    )

    assert listed.market_id == market.market_id
    assert listed.prices_7d_ago is None


async def test_a_book_cut_short_of_an_outcome_row_has_no_baseline_and_the_rest_are_answered(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "A book the batch snapshot cannot price is left out and
    logged": one damaged book must not blank the whole list. A three-outcome
    book with its middle row deleted still passes `refuse_unpriceable` with
    two rows, and a trade on its last outcome no longer has a place in the
    replay's `q`. Its delta goes; its price and its neighbour stay."""
    damaged = Upstream(outcomes=3)
    await open_at_zero(session, damaged)
    await hold(session, damaged, user_id=uuid.uuid4(), quantity=Decimal("50"), outcome=2)
    await drop_outcome_row(session, damaged.market_id, position=1)
    healthy = Upstream()
    await open_at_zero(session, healthy)

    result = await _snapshot_service().list_snapshots(
        session,
        [damaged.market_id, healthy.market_id],
        now=_a_day_past_the_window(),
    )

    assert [(listed.market_id, listed.prices_7d_ago) for listed in result] == [
        (damaged.market_id, None),
        (healthy.market_id, None),
    ]


# =========================================================================
# The read it makes
# =========================================================================
async def test_every_markets_baseline_is_read_in_one_statement(
    session: AsyncSession,
) -> None:
    """No N+1: three markets cost one statement reading `ledger.entries`."""
    markets = [Upstream(), Upstream(), Upstream()]
    for market in markets:
        await warm(session, market)

    with capture_sql() as statements:
        await _snapshot_service().list_snapshots(
            session, [market.market_id for market in markets], now=_a_day_past_the_window()
        )

    entry_reads = mentioning(statements, "entries")
    assert len(entry_reads) == 1, f"expected one statement, sent:\n{statements}"
