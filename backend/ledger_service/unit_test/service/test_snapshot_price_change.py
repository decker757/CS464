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

The baseline is the newest trade's stored `after_q`, and a market whose newest
trade has none is replayed (#643). A trade from before `after_q` existed is
made here by deleting the key from a real trade's context: `ledger.entries` is
append-only and `ledger.transactions` is not.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
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


async def _strip_after_q(session: AsyncSession, transaction_id: uuid.UUID) -> None:
    """The trade as one written before #643 stored it. Committed."""
    await session.execute(
        text(
            "UPDATE ledger.transactions SET context = context - 'after_q' "
            "WHERE id = :id"
        ),
        {"id": transaction_id},
    )
    await session.commit()


async def _store_after_q(
    session: AsyncSession, transaction_id: uuid.UUID, after_q: list[str]
) -> None:
    """Overwrite a trade's stored `after_q`. Committed."""
    await session.execute(
        text(
            "UPDATE ledger.transactions "
            "SET context = jsonb_set(context, '{after_q}', CAST(:after_q AS jsonb)) "
            "WHERE id = :id"
        ),
        {"id": transaction_id, "after_q": json.dumps(after_q)},
    )
    await session.commit()


def _replay_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO and "replayed" in record.getMessage()
    ]


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
    its prices. Only a feed from before `after_q` is replayed (see `gapped`),
    so a gap there loses its baseline; a newer feed is priced as stored."""
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


async def test_the_seven_day_price_is_one_statement_when_every_market_s_newest_trade_carries_after_q(
    session: AsyncSession,
) -> None:
    """AC: one statement for any number of markets when every newest trade
    carries `after_q`, and it is the `LATERAL` read that takes one index
    entry per market rather than every trade (#643). Three traded markets,
    each with the price its third trade left. Catches: the replay of every
    trade kept as the first read, or a `DISTINCT ON` that sorts them all."""
    scenarios = [await traded(session) for _ in range(3)]

    with capture_sql() as statements:
        result = await _snapshot_service().list_snapshots(
            session,
            [scenario.market_id for scenario in scenarios],
            now=_a_day_past_the_window(),
        )

    entry_reads = mentioning(statements, "entries")
    assert len(entry_reads) == 1, f"expected one statement, sent:\n{statements}"
    assert "LATERAL" in entry_reads[0]
    assert "DISTINCT" not in entry_reads[0]
    assert [_priced(listed.prices_7d_ago) for listed in result] == [
        _wire(scenario.upstream.outcomes, 3) for scenario in scenarios
    ]


async def test_a_market_whose_newest_old_trade_lacks_after_q_falls_back_and_is_logged(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """AC: `prices_7d_ago` is unchanged for a market whose trades do not carry
    `after_q`, with the fallback logged. The window starts at the second
    trade, whose key is deleted: the replay gives version 2's prices, not the
    first trade's stored ones (version 1) and not the third's (version 3).
    One INFO line names the market."""
    caplog.set_level(logging.INFO, logger="service.price_history")
    scenario = await traded(session)
    times = await entry_times(session, [scenario.b_buy.transaction_id])
    await _strip_after_q(session, scenario.b_buy.transaction_id)

    [listed] = await _snapshot_service().list_snapshots(
        session,
        [scenario.market_id],
        now=times[scenario.b_buy.transaction_id] + _WINDOW,
    )

    assert _priced(listed.prices_7d_ago) == _wire(scenario.upstream.outcomes, 2)
    [line] = _replay_lines(caplog)
    assert str(scenario.market_id) in line


async def test_the_fallback_is_per_market_not_per_batch(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The markets lacking `after_q` are replayed alone, together, in one more
    statement: two stripped markets cost two statements in all, not three.
    The intact market's newest trade stores a `q` of [100, 0], which its own
    replay does not reach, so its price shows which read answered it: the
    stored one, 0.7311 (1 / (1 + e^-1)). Catches: one missing key replaying
    the whole batch, or a replay statement per market."""
    caplog.set_level(logging.INFO, logger="service.price_history")
    stripped = [await traded(session) for _ in range(2)]
    intact = await traded(session)
    for scenario in stripped:
        await _strip_after_q(session, scenario.a_sell.transaction_id)
    await _store_after_q(session, intact.a_sell.transaction_id, ["100.0000", "0.0000"])

    with capture_sql() as statements:
        result = await _snapshot_service().list_snapshots(
            session,
            [*(scenario.market_id for scenario in stripped), intact.market_id],
            now=_a_day_past_the_window(),
        )

    assert len(mentioning(statements, "entries")) == 2, statements
    assert [_priced(listed.prices_7d_ago) for listed in result] == [
        *(_wire(scenario.upstream.outcomes, 3) for scenario in stripped),
        [
            (intact.upstream.outcomes[0], 0, Decimal("0.7311")),
            (intact.upstream.outcomes[1], 1, Decimal("0.2689")),
        ],
    ]
    lines = _replay_lines(caplog)
    assert len(lines) == 2, lines
    for scenario in stripped:
        assert [line for line in lines if str(scenario.market_id) in line], lines
