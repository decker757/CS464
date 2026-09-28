"""Valuing holdings at liquidation, as pure arithmetic. [T-4] #24, ADR 0018

`core/pricing.py::liquidation_values_of(q, b, holdings) -> list[Decimal]`:
one value per outcome, the holdings sold one outcome at a time in position
order, each through `trade_cost_of` on the `q` the previous sale left. A sale
below a tick is `0.0000` and leaves `q` alone; `InsufficientSharesOutstanding`
propagates. "A user's holdings in one market are valued as one sequence of
sales". No session, no database.

The first block holds the literals the Postgres suites pin against the
engine, the way `test_sell.py` holds #23's.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

import pytest

from unit_test.portfolio_fixtures import (
    ADR_AVERAGE,
    ADR_B,
    ADR_BASIS,
    ADR_PRICE,
    ADR_QUANTITY,
    ADR_VALUE,
    AFTER_DUST_VALUE,
    AFTER_MOVED_DUST_VALUE,
    DUST,
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
    THREE_BUYS_BASIS,
    THREE_BUYS_VALUE,
    TWO_BUYS,
    TWO_BUYS_BASIS,
    TWO_BUYS_VALUE,
    sequence_values,
)
from unit_test.sell_fixtures import BASIS, HELD, REMAINING_BASIS, REMAINING_QUANTITY
from unit_test.trade_fixtures import B, QUANTUM, errors, lmsr, pricing

ZERO = Decimal(0)
_ZERO_Q = [ZERO, ZERO]


def _liquidation_values_of(q, b, holdings):
    """Reached through the module at call time, so a missing name fails each
    test rather than collection."""
    return pricing().liquidation_values_of(q, b, holdings)


def _raw(q, b, index, quantity):
    delta = [ZERO] * len(q)
    delta[index] = quantity
    return lmsr().cost_to_trade(list(q), b, delta)


def _raw_total(q, b, holdings):
    return lmsr().cost_to_trade(list(q), b, [-held for held in holdings]).copy_abs()


def _bought(q, b, index, quantity):
    """`(q after, basis)` for one buy, ceiled as the trade path charges it."""
    cost = pricing().trade_cost_of(q, b, index=index, side="buy", quantity=quantity)
    return list(cost.after_q), cost.magnitude


def _sold(q, b, index, quantity):
    cost = pricing().trade_cost_of(q, b, index=index, side="sell", quantity=quantity)
    return list(cost.after_q), cost.magnitude


def _price(q, b, index):
    return pricing().quantize_price(lmsr().prices(list(q), b)[index])


# =========================================================================
# The literals the Postgres suites pin
# =========================================================================
def test_adr_0018_worked_example_is_what_the_engine_says() -> None:
    q, basis = _bought(_ZERO_Q, ADR_B, 0, ADR_QUANTITY)
    assert basis == ADR_BASIS
    assert _sold(q, ADR_B, 0, ADR_QUANTITY)[1] == ADR_VALUE
    assert _price(q, ADR_B, 0) == ADR_PRICE
    assert (ADR_BASIS / ADR_QUANTITY).quantize(QUANTUM, rounding=ROUND_HALF_UP) == ADR_AVERAGE


def test_the_complete_set_is_what_the_engine_says() -> None:
    q, yes_basis = _bought(_ZERO_Q, ADR_B, 0, ADR_QUANTITY)
    q, no_basis = _bought(q, ADR_B, 1, ADR_QUANTITY)
    assert (yes_basis, no_basis) == (ADR_BASIS, SET_NO_BASIS)
    assert q == [ADR_QUANTITY, ADR_QUANTITY]

    assert sequence_values(q, ADR_B, [ADR_QUANTITY, ADR_QUANTITY]) == list(SET_VALUES)
    assert sum(SET_VALUES) == SET_TOTAL
    separately = sum(_sold(q, ADR_B, i, ADR_QUANTITY)[1] for i in (0, 1))
    assert separately == SET_SEPARATE_TOTAL


def test_23s_holding_is_what_the_engine_says() -> None:
    q, basis = _bought(_ZERO_Q, B, 0, HELD)
    assert basis == BASIS
    assert _sold(q, B, 0, HELD)[1] == HOLDING_VALUE
    assert _price(q, B, 0) == HOLDING_PRICE

    remaining_q = [REMAINING_QUANTITY, ZERO]
    assert _sold(remaining_q, B, 0, REMAINING_QUANTITY)[1] == REMAINING_VALUE

    assert _sold(q, ADR_B, 0, HELD)[1] != HOLDING_VALUE
    assert _sold([HELD, ZERO], ADR_B, 0, HELD)[1] == OTHER_B_VALUE


@pytest.mark.parametrize(
    ("basis", "quantity", "expected"),
    [(BASIS, HELD, HOLDING_AVERAGE), (REMAINING_BASIS, REMAINING_QUANTITY, REMAINING_AVERAGE)],
)
def test_average_entry_rounding_is_load_bearing(
    basis: Decimal, quantity: Decimal, expected: Decimal
) -> None:
    """Half-up and floor disagree on both, so the service tests pinning these
    catch a floored average."""
    exact = basis / quantity
    assert exact.quantize(QUANTUM, rounding=ROUND_HALF_UP) == expected
    assert exact.quantize(QUANTUM, rounding=ROUND_FLOOR) != expected


def test_the_exact_tick_book_prices_on_the_tick_both_ways() -> None:
    """At q = (0, L), buying 2L of outcome 0 costs exactly L. On this engine
    at b = 137 the raw figure lands on the tick with no residue either way,
    so the buyer's P&L is exactly 0.0000."""
    q, _ = _bought(_ZERO_Q, B, 1, EXACT_TICK_LEAD)
    raw = _raw(q, B, 0, EXACT_TICK_BUY)
    assert raw == EXACT_TICK_BASIS, f"not on the tick: {raw}"

    q, basis = _bought(q, B, 0, EXACT_TICK_BUY)
    assert basis == EXACT_TICK_BASIS
    assert _raw(q, B, 0, -EXACT_TICK_BUY).copy_abs() == EXACT_TICK_BASIS
    assert _sold(q, B, 0, EXACT_TICK_BUY)[1] == EXACT_TICK_BASIS


@pytest.mark.parametrize(
    ("buys", "basis", "value"),
    [(TWO_BUYS, TWO_BUYS_BASIS, TWO_BUYS_VALUE), (THREE_BUYS, THREE_BUYS_BASIS, THREE_BUYS_VALUE)],
)
def test_positions_built_from_several_buys_are_what_the_engine_says(
    buys: tuple[Decimal, ...], basis: Decimal, value: Decimal
) -> None:
    q = list(_ZERO_Q)
    total = ZERO
    for quantity in buys:
        q, cost = _bought(q, B, 0, quantity)
        total += cost
    assert total == basis
    assert _sold(q, B, 0, sum(buys))[1] == value
    assert value - basis == -QUANTUM * len(buys), (
        "each of these loses one tick per buy; the case exists to show that"
    )


def test_a_dust_sale_is_below_a_tick() -> None:
    """0.0001 shares at q = (0.0001, 0) sells for 0.00005…: the floor is zero,
    and the trade route refuses it."""
    raw = _raw([DUST, ZERO], B, 0, -DUST).copy_abs()
    assert 0 < raw < QUANTUM
    with pytest.raises(errors().ProceedsBelowTick):
        _sold([DUST, ZERO], B, 0, DUST)


def test_the_after_dust_figures_are_what_the_engine_says() -> None:
    q = [DUST, HELD]
    assert _sold(q, B, 1, HELD)[1] == AFTER_DUST_VALUE
    assert _sold([ZERO, HELD], B, 1, HELD)[1] == AFTER_MOVED_DUST_VALUE


# =========================================================================
# liquidation_values_of
# =========================================================================
@pytest.mark.parametrize(
    ("q", "b", "holdings", "expected"),
    [
        ([ADR_QUANTITY, ZERO], ADR_B, [ADR_QUANTITY, ZERO], [ADR_VALUE, Decimal("0.0000")]),
        ([HELD, ZERO], B, [HELD, ZERO], [HOLDING_VALUE, Decimal("0.0000")]),
        ([ZERO, HELD], B, [ZERO, HELD], [Decimal("0.0000"), HOLDING_VALUE]),
    ],
)
def test_one_held_outcome_is_one_sale(q, b, holdings, expected) -> None:
    values = _liquidation_values_of(q, b, holdings)

    assert values == expected
    index = next(i for i, held in enumerate(holdings) if held)
    assert values[index] == _sold(q, b, index, holdings[index])[1]
    assert [str(v) for v in values] == [str(v) for v in expected]


def test_nothing_held_is_0_0000_in_every_outcome() -> None:
    values = _liquidation_values_of([HELD, Decimal("41.0000")], B, [ZERO, ZERO])

    assert [str(v) for v in values] == ["0.0000", "0.0000"]


def test_a_complete_set_is_sold_in_sequence_not_each_alone() -> None:
    """ADR 0018: 500 YES and 500 NO at q = (500, 500), b = 100, is a complete
    set worth 500. In sequence 68.6431 + 431.3568; each alone 68.6431 twice."""
    q = [ADR_QUANTITY, ADR_QUANTITY]

    values = _liquidation_values_of(q, ADR_B, [ADR_QUANTITY, ADR_QUANTITY])

    assert values == list(SET_VALUES)
    assert sum(values) == SET_TOTAL
    assert sum(values) != SET_SEPARATE_TOTAL


_SEQUENCES = [
    # (q, b, holdings): a second holder in every one, so q != holdings.
    ([Decimal("54.3333"), Decimal("48.7777")], B, [Decimal("54.3333"), Decimal("41.0000")]),
    ([Decimal("13.3333"), Decimal("46.5555")], B, [Decimal("13.3333"), Decimal("41.0000")]),
    (
        [Decimal("61.6161"), Decimal("22.2222"), Decimal("30.3030")],
        B,
        [Decimal("41.0000"), Decimal("22.2222"), Decimal("7.7777")],
    ),
    ([Decimal("500.0000"), Decimal("500.0000")], ADR_B, [Decimal("500.0000"), Decimal("500.0000")]),
]


@pytest.mark.parametrize(("q", "b", "holdings"), _SEQUENCES)
def test_each_sale_is_priced_on_the_book_the_previous_one_left(q, b, holdings) -> None:
    values = _liquidation_values_of(q, b, holdings)

    current = list(q)
    for index, held in enumerate(holdings):
        current, proceeds = _sold(current, b, index, held)
        assert values[index] == proceeds, f"outcome {index}"


@pytest.mark.parametrize(("q", "b", "holdings"), _SEQUENCES)
def test_the_sequence_is_the_cost_difference_less_the_floors(q, b, holdings) -> None:
    """Path independence: the unrounded proceeds total C(q) − C(q − h) in any
    order. Each sale floors on its own, so the total lands within one tick per
    sale below it, and never above."""
    values = _liquidation_values_of(q, b, holdings)

    raw = _raw_total(q, b, holdings)
    sales = sum(1 for held in holdings if held)
    assert sum(values) <= raw
    assert sum(values) > raw - QUANTUM * sales


def test_position_order_is_the_order_the_holdings_are_sold_in() -> None:
    """At b = 137, q = (13.3333, 46.5555), holding (13.3333, 41.0000), the
    rounded total depends on the order: 28.1430 selling outcome 0 first,
    28.1429 selling outcome 1 first."""
    q = [Decimal("13.3333"), Decimal("46.5555")]
    holdings = [Decimal("13.3333"), Decimal("41.0000")]
    after_one, first_one = _sold(q, B, 1, holdings[1])
    reverse_total = first_one + _sold(after_one, B, 0, holdings[0])[1]
    assert reverse_total == Decimal("28.1429")

    values = _liquidation_values_of(q, B, holdings)

    assert sum(values) == Decimal("28.1430")


def test_a_sub_tick_sale_is_0_0000_and_does_not_move_the_book() -> None:
    """Dust in outcome 0 is refused by the route, so outcome 1 is sold on the
    book as it was: 29.8426, where a book the dust sale had moved pays
    29.8427."""
    values = _liquidation_values_of([DUST, HELD], B, [DUST, HELD])

    assert [str(v) for v in values] == ["0.0000", str(AFTER_DUST_VALUE)]
    assert values[1] != AFTER_MOVED_DUST_VALUE


def test_a_holding_above_q_raises_insufficient_shares_outstanding() -> None:
    with pytest.raises(errors().InsufficientSharesOutstanding):
        _liquidation_values_of([Decimal("10.0000"), ZERO], B, [HELD, ZERO])


def test_a_later_holding_above_q_raises_too() -> None:
    """The check is per sale, not only on the first."""
    with pytest.raises(errors().InsufficientSharesOutstanding):
        _liquidation_values_of(
            [HELD, Decimal("10.0000")], B, [HELD, Decimal("41.0000")]
        )


def test_a_ceiling_would_change_every_value_here() -> None:
    """A property of the constants: the proceeds these tests pin are not on
    the tick, so a ceiled sale would be caught."""
    for q, b, index, quantity in [
        ([ADR_QUANTITY, ZERO], ADR_B, 0, ADR_QUANTITY),
        ([HELD, ZERO], B, 0, HELD),
        ([ADR_QUANTITY, ADR_QUANTITY], ADR_B, 0, ADR_QUANTITY),
    ]:
        raw = _raw(q, b, index, -quantity).copy_abs()
        assert raw.quantize(QUANTUM, rounding=ROUND_CEILING) != raw.quantize(
            QUANTUM, rounding=ROUND_FLOOR
        )
