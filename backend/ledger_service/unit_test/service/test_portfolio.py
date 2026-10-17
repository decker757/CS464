"""Valuing a portfolio. [T-4] #24, ADR 0018

Business rules, driven through `service/portfolio.py::portfolio_of_user` with
no HTTP inbound. The route is `unit_test/controller/test_portfolio_routes.py`'s;
the grant, the lock, the market_service hop, the statement count and the
damaged book are `test_portfolio_reads.py`'s; the pure sequence is
`unit_test/core/test_portfolio_figures.py`'s.

**Every book is opened at zero by its first touch, and every holding is made
by a real trade** — never `warm()`, never a position inserted by hand.

**The numbers are ADR 0018's and #23's.** Everything is compared with `==` on
a `Decimal`, and the ADR's figures also as strings.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import terms_client_over
from unit_test.portfolio_fixtures import (
    ADR_AVERAGE,
    ADR_B,
    ADR_BASIS,
    ADR_PNL,
    ADR_PRICE,
    ADR_QUANTITY,
    ADR_VALUE,
    AFTER_DUST_VALUE,
    DUST,
    DUST_SOLD,
    EXACT_TICK_BASIS,
    EXACT_TICK_BUY,
    EXACT_TICK_LEAD,
    HOLDING_AVERAGE,
    HOLDING_PRICE,
    HOLDING_VALUE,
    OTHER_B_VALUE,
    REMAINING_AVERAGE,
    REMAINING_VALUE,
    SET_NO_BASIS,
    SET_SEPARATE_TOTAL,
    SET_TOTAL,
    SET_VALUES,
    THREE_BUYS,
    TWO_BUYS,
    entries_sum,
    funded,
    market_at,
    read_portfolio,
    row,
    sequence_values,
)
from unit_test.sell_fixtures import (
    BASIS,
    HELD,
    REMAINING_BASIS,
    REMAINING_QUANTITY,
    SOLD,
    TICK,
    hold,
    q_now,
    sell,
    snapshot_module,
    version_of,
)
from unit_test.trade_fixtures import (
    B,
    ZERO,
    Upstream,
    accounts_module,
    entities,
    preview,
    pricing,
    token,
)

OTHER = Decimal("41.0000")


async def _preview_sale(
    session: AsyncSession, upstream: Upstream, *, outcome: int, quantity: Decimal
):
    """The trade route's own quote for this sell, at the current state."""
    return await preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[outcome],
        side=pricing().Side.SELL,
        quantity=quantity,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )


async def _buy_all(
    session: AsyncSession, upstream: Upstream, user_id: uuid.UUID, quantities
) -> None:
    for quantity in quantities:
        await hold(session, upstream, user_id=user_id, quantity=quantity)


def _pnl_bound_holds(pnl: Decimal, trades: int) -> bool:
    """Never a gain, and less than one tick lost per trade involved."""
    return -TICK * trades < pnl <= ZERO


# =========================================================================
# ADR 0018's worked example
# =========================================================================
async def test_adr_0018_worked_example_is_valued_at_liquidation_not_marginal(
    session: AsyncSession,
) -> None:
    upstream = await market_at(session, ADR_B)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id, quantity=ADR_QUANTITY)

    result = await read_portfolio(session, user_id)

    position = row(result, upstream.market_id, 0)
    assert position.outcome_id == upstream.outcomes[0]
    assert position.quantity == ADR_QUANTITY
    assert str(position.cost_basis) == str(ADR_BASIS)
    assert str(position.value) == str(ADR_VALUE)
    assert str(position.unrealized_pnl) == str(ADR_PNL)
    assert str(position.price) == str(ADR_PRICE)
    assert str(position.average_entry_price) == str(ADR_AVERAGE)
    assert position.state_version == 1
    assert position.value != position.quantity * position.price


# =========================================================================
# value, price and P&L
# =========================================================================
async def test_a_markets_only_position_is_worth_the_previewed_proceeds_of_selling_it(
    session: AsyncSession,
) -> None:
    """The only held outcome in a market is one sale, so its value is the
    trade route's proceeds for that sell at the same `state_version`: quoted
    by the preview, then executed at that version. Two traders have moved the
    book, so `q` is not the holding."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await hold(session, upstream, user_id=other_id, quantity=OTHER, outcome=1)
    await hold(session, upstream, user_id=other_id, quantity=Decimal("7.7777"))

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)
    await session.rollback()
    quote = await _preview_sale(session, upstream, outcome=0, quantity=HELD)

    assert quote.state_version == position.state_version
    assert str(position.value) == str(quote.total)

    trade = await sell(
        session,
        upstream,
        user_id=user_id,
        quantity=HELD,
        state_version=position.state_version,
    )
    assert str(trade.total) == str(position.value)


async def test_price_is_the_snapshots_price_at_the_same_state_version(
    session: AsyncSession,
) -> None:
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await hold(session, upstream, user_id=user_id, quantity=SOLD, outcome=1)
    await hold(session, upstream, user_id=other_id, quantity=OTHER, outcome=1)

    result = await read_portfolio(session, user_id)
    await session.rollback()
    snapshot = await snapshot_module().snapshot(
        session,
        upstream.market_id,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )

    by_outcome = {p.outcome_id: p.price for p in snapshot.prices}
    for position in [row(result, upstream.market_id, 0), row(result, upstream.market_id, 1)]:
        assert position.state_version == snapshot.state_version
        assert str(position.price) == str(by_outcome[position.outcome_id])


@pytest.mark.parametrize(
    ("b", "buys", "pnl"),
    [
        (ADR_B, (ADR_QUANTITY,), ADR_PNL),
        (B, (HELD,), Decimal("-0.0001")),
        (B, TWO_BUYS, Decimal("-0.0002")),
        (B, THREE_BUYS, Decimal("-0.0003")),
    ],
    ids=["adr-one-buy", "23-one-buy", "two-buys", "three-buys"],
)
async def test_a_position_on_a_book_nobody_else_traded_never_shows_a_gain(
    session: AsyncSession, b: Decimal, buys: tuple[Decimal, ...], pnl: Decimal
) -> None:
    """n buys and one sale: −(n + 1) ticks < P&L ≤ 0. The three-buy case is
    the one that broke the original "two ticks" criterion."""
    upstream = await market_at(session, b)
    user_id, _ = await funded(session)
    await _buy_all(session, upstream, user_id, buys)

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)

    assert position.unrealized_pnl == pnl
    assert position.unrealized_pnl == position.value - position.cost_basis
    assert _pnl_bound_holds(position.unrealized_pnl, len(buys) + 1)


async def test_a_buy_that_lands_on_the_tick_shows_exactly_0_0000(
    session: AsyncSession,
) -> None:
    """At q = (0, 13.3333), buying 26.6666 of outcome 0 costs exactly 13.3333,
    and selling it back pays exactly 13.3333. The buyer is the only trader
    since, so this is the bound's upper edge."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, upstream, user_id=other_id, quantity=EXACT_TICK_LEAD, outcome=1)
    await hold(session, upstream, user_id=user_id, quantity=EXACT_TICK_BUY)

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)

    assert position.cost_basis == EXACT_TICK_BASIS
    assert position.value == EXACT_TICK_BASIS
    assert str(position.unrealized_pnl) == "0.0000"


@pytest.mark.parametrize(
    ("outcome", "value", "pnl"),
    [
        (0, Decimal("33.7688"), Decimal("3.9260")),
        (1, Decimal("25.8007"), Decimal("-4.0421")),
    ],
    ids=["others-bought-the-same-outcome", "others-bought-against-it"],
)
async def test_other_traders_move_the_value_of_a_position(
    session: AsyncSession, outcome: int, value: Decimal, pnl: Decimal
) -> None:
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await hold(session, upstream, user_id=other_id, quantity=OTHER, outcome=outcome)

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)

    assert position.cost_basis == BASIS
    assert position.value == value
    assert position.unrealized_pnl == pnl


async def test_a_dust_position_is_worth_zero_and_loses_its_whole_basis(
    session: AsyncSession,
) -> None:
    """Reached with #23's trades: 54.3333 bought, 54.3332 sold, 0.0001 left at
    a basis of 0.0001. Its sale is below a tick, so the route would refuse it."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await sell(session, upstream, user_id=user_id, quantity=DUST_SOLD)

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)

    assert (position.quantity, position.cost_basis) == (DUST, DUST)
    assert str(position.value) == "0.0000"
    assert position.unrealized_pnl == -position.cost_basis
    assert str(position.average_entry_price) == "1.0000"
    assert str(position.price) == "0.5000"


async def test_a_dust_step_does_not_move_the_book_for_the_next_outcome(
    session: AsyncSession,
) -> None:
    """Dust in outcome 0, then 54.3333 of outcome 1. The dust sale is refused,
    so outcome 1 sells at q = (0.0001, 54.3333) for 29.8426. A sequence that
    moved the book first would pay 29.8427."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await sell(session, upstream, user_id=user_id, quantity=DUST_SOLD)
    await hold(session, upstream, user_id=user_id, outcome=1)
    assert await q_now(session, upstream.market_id) == [DUST, HELD]

    result = await read_portfolio(session, user_id)

    assert str(row(result, upstream.market_id, 0).value) == "0.0000"
    assert row(result, upstream.market_id, 1).value == AFTER_DUST_VALUE


# =========================================================================
# Which rows, and their quantities
# =========================================================================
async def test_a_sold_out_position_is_absent_and_a_partly_sold_one_shows_what_remains(
    session: AsyncSession,
) -> None:
    """#23's partial sell: 54.3333 at 29.8428, 10.0000 sold, 44.3333 at
    24.3503 remaining. Outcome 1 was bought and sold out, and keeps its row at
    0.0000/0.0000 in the table — which is not shown."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await sell(session, upstream, user_id=user_id, quantity=SOLD)
    await hold(session, upstream, user_id=user_id, quantity=OTHER, outcome=1)
    await sell(session, upstream, user_id=user_id, quantity=OTHER, outcome=1)

    result = await read_portfolio(session, user_id)

    assert [(p.market_id, p.outcome_position) for p in result.positions] == [
        (upstream.market_id, 0)
    ]
    position = result.positions[0]
    assert (position.quantity, position.cost_basis) == (REMAINING_QUANTITY, REMAINING_BASIS)
    assert position.value == REMAINING_VALUE
    assert position.unrealized_pnl == REMAINING_VALUE - REMAINING_BASIS
    assert position.average_entry_price == REMAINING_AVERAGE


async def test_average_entry_is_basis_over_quantity_rounded_half_up(
    session: AsyncSession,
) -> None:
    """29.8428 / 54.3333 = 0.54925…: half-up 0.5493, where a floor gives
    0.5492."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)

    assert str(position.average_entry_price) == str(HOLDING_AVERAGE)
    assert position.value == HOLDING_VALUE
    assert position.price == HOLDING_PRICE


async def test_yes_and_no_in_one_market_are_valued_as_one_sequence_of_sales(
    session: AsyncSession,
) -> None:
    """Outcome 0 sells first, at the current state — which the preview can
    quote. Outcome 1 then sells on the `q` that sale left, which no preview
    can quote, so it is computed from the engine."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await hold(session, upstream, user_id=other_id, quantity=Decimal("7.7777"), outcome=1)
    await hold(session, upstream, user_id=user_id, quantity=OTHER, outcome=1)

    result = await read_portfolio(session, user_id)
    await session.rollback()
    first = await _preview_sale(session, upstream, outcome=0, quantity=HELD)
    q = await q_now(session, upstream.market_id)

    yes, no = row(result, upstream.market_id, 0), row(result, upstream.market_id, 1)
    assert str(yes.value) == str(first.total)
    expected = sequence_values(q, B, [HELD, OTHER])
    assert [yes.value, no.value] == expected

    alone = await _preview_sale(session, upstream, outcome=1, quantity=OTHER)
    assert no.value != alone.total, "outcome 1 was valued alone at the current q"


async def test_a_complete_set_is_worth_its_size_less_the_floors(
    session: AsyncSession,
) -> None:
    """ADR 0018: 500 YES then 500 NO from a zero book at b = 100 is a complete
    set worth 500. Four trades — two buys, two sales in the valuation — so
    positions_value is within four ticks of 500 and never above it. On this
    engine, 499.9999; valued one position at a time, 137.2862."""
    upstream = await market_at(session, ADR_B)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id, quantity=ADR_QUANTITY)
    await hold(session, upstream, user_id=user_id, quantity=ADR_QUANTITY, outcome=1)

    result = await read_portfolio(session, user_id)

    yes, no = row(result, upstream.market_id, 0), row(result, upstream.market_id, 1)
    assert (yes.cost_basis, no.cost_basis) == (ADR_BASIS, SET_NO_BASIS)
    assert (yes.value, no.value) == SET_VALUES
    assert result.positions_value == SET_TOTAL
    assert ADR_QUANTITY - 4 * TICK < result.positions_value <= ADR_QUANTITY
    assert result.positions_value != SET_SEPARATE_TOTAL


async def test_two_markets_are_each_valued_against_their_own_book(
    session: AsyncSession,
) -> None:
    """The same 54.3333 in a b = 137 market and a b = 100 one: 29.8427 and
    30.8122. Priced against each other's `b`, they swap. Another trader's
    round trip in the second market leaves its `q` where it was and its
    `state_version` at 3, so each row must carry its own book's version."""
    first = await market_at(session, B)
    second = await market_at(session, ADR_B)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, second, user_id=other_id, quantity=SOLD, outcome=1)
    await sell(session, second, user_id=other_id, quantity=SOLD, outcome=1)
    await hold(session, first, user_id=user_id)
    await hold(session, second, user_id=user_id)
    assert await q_now(session, second.market_id) == [HELD, ZERO]

    result = await read_portfolio(session, user_id)

    assert row(result, first.market_id, 0).value == HOLDING_VALUE
    assert row(result, first.market_id, 0).state_version == 1
    second_row = row(result, second.market_id, 0)
    assert second_row.value == OTHER_B_VALUE
    assert second_row.state_version == await version_of(session, second.market_id)
    assert second_row.state_version == 3


async def test_only_the_callers_positions_appear(session: AsyncSession) -> None:
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    other_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await hold(session, upstream, user_id=other_id, quantity=OTHER, outcome=1)
    await hold(session, upstream, user_id=other_id, quantity=SOLD)

    mine = await read_portfolio(session, user_id)
    theirs = await read_portfolio(session, other_id)
    nobody = await read_portfolio(session, uuid.uuid4())

    assert mine.user_id == user_id
    assert [(p.outcome_position, p.quantity) for p in mine.positions] == [(0, HELD)]
    assert [(p.outcome_position, p.quantity) for p in theirs.positions] == [
        (0, SOLD),
        (1, OTHER),
    ]
    assert nobody.positions == []


async def test_positions_come_back_ordered_by_market_id_then_outcome_position(
    session: AsyncSession,
) -> None:
    """Bought in the reverse of that order — the higher market id first, and
    outcome 1 before outcome 0 in each — so neither insertion order nor
    `created_at` can pass for it."""
    markets = sorted(
        [await market_at(session), await market_at(session)],
        key=lambda m: m.market_id,
        reverse=True,
    )
    user_id, _ = await funded(session)
    for upstream in markets:
        await hold(session, upstream, user_id=user_id, quantity=SOLD, outcome=1)
        await hold(session, upstream, user_id=user_id, quantity=SOLD, outcome=0)

    result = await read_portfolio(session, user_id)

    expected = [(m.market_id, i) for m in reversed(markets) for i in (0, 1)]
    assert [(p.market_id, p.outcome_position) for p in result.positions] == expected
    assert [p.outcome_id for p in result.positions] == [
        m.outcomes[i] for m in reversed(markets) for i in (0, 1)
    ]


# =========================================================================
# The totals
# =========================================================================
async def test_an_empty_portfolio_is_the_balance_and_nothing_else(
    session: AsyncSession,
) -> None:
    user_id, credits = await funded(session)

    result = await read_portfolio(session, user_id)

    assert result.positions == []
    assert result.balance == credits
    assert str(result.positions_value) == "0.0000"
    assert result.net_worth == result.balance


async def test_a_user_whose_positions_are_all_sold_out_is_just_their_balance(
    session: AsyncSession,
) -> None:
    """Rows at 0.0000/0.0000 exist and are not shown; the balance still is.
    #23's round trip leaves the user one tick down."""
    upstream = await market_at(session)
    user_id, credits = await funded(session)
    await hold(session, upstream, user_id=user_id)
    await sell(session, upstream, user_id=user_id, quantity=HELD)

    result = await read_portfolio(session, user_id)

    assert result.positions == []
    assert result.balance == credits - TICK
    assert result.net_worth == result.balance


async def test_balance_is_the_users_entries_and_net_worth_is_balance_plus_values(
    session: AsyncSession,
) -> None:
    first = await market_at(session, B)
    second = await market_at(session, ADR_B)
    user_id, credits = await funded(session)
    await hold(session, first, user_id=user_id)
    await hold(session, first, user_id=user_id, quantity=OTHER, outcome=1)
    await hold(session, second, user_id=user_id, quantity=SOLD)
    await sell(session, first, user_id=user_id, quantity=SOLD)

    result = await read_portfolio(session, user_id)
    await session.rollback()

    assert result.balance == await entries_sum(session, user_id)
    assert result.balance < credits
    assert result.positions_value == sum(p.value for p in result.positions)
    assert result.net_worth == result.balance + result.positions_value
    account = await accounts_module().find(
        session, entities().AccountKind.USER, user_id
    )
    assert result.account_id == account.id


async def test_a_closed_market_is_valued_at_its_frozen_book(
    session: AsyncSession,
) -> None:
    """ADR 0018: after close, the value is what a sale would have paid at the
    last traded state. The preview on a warm book still quotes it."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    upstream.closes()

    position = row(await read_portfolio(session, user_id), upstream.market_id, 0)
    await session.rollback()
    quote = await _preview_sale(session, upstream, outcome=0, quantity=HELD)

    assert position.value == HOLDING_VALUE
    assert str(position.value) == str(quote.total)
    assert position.state_version == quote.state_version
