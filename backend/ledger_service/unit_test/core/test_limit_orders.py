"""The pure core for limit orders: fills, the cascade and the crossing check.
[T-7] #27, [T-6] #26

Pins ADR 0024, "Resting orders fill against the AMM inside the trade that
moved the price", and the decisions under it: "An order is executable when
its floored fill passes the per-fill limit check; a resting order is one that
is not", "Priority is price, then an identity `seq` assigned under the book
lock", "A placement that would cross the book is refused,
`order_crosses_book`", and "The cascade loops until no waiting order is
executable, and passes over an order it cannot fill".

Every expected figure comes from the oracle below or from the contract's
worked example. The oracle is a second implementation in stdlib Decimal at
its own precision and imports nothing from `core`. It finds a fill by
bisecting over ticks on the property a fill must have, the order's own price
no further than its limit, not by the closed form the module uses.
Pure, so no database.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Context,
    Decimal,
    localcontext,
)

import pytest

from core.errors import (
    CostBelowTick,
    InsufficientSharesOutstanding,
    LedgerError,
    OrderCrossesBook,
    ProceedsBelowTick,
    QuantityTooLarge,
)
from core.limit_orders import (
    PlannedFill,
    RestingOrder,
    cascade_of,
    executable_fill_of,
    fill_quantity_of,
    refuse_if_crosses_book,
)
from core.pricing import Side, trade_cost_of

# The repo's default `b`, and the one the contract's worked example uses.
B = Decimal("100")

# The oracle's own tick and precision. Ten digits above the engine's 50, so
# the two do not round alike.
_TICK = Decimal("0.0001")
_ORACLE_PRECISION = 60


@dataclass(frozen=True)
class _ExpectedFill:
    """One fill as the oracle prices it, at the `q` the fill before it left."""

    seq: int
    quantity: Decimal
    magnitude: Decimal
    after_q: tuple[Decimal, ...]


# --- the oracle: stdlib Decimal only, nothing from core ---------------------
def _oracle_context() -> AbstractContextManager[Context]:
    return localcontext(Context(prec=_ORACLE_PRECISION))


def _oracle_price(q: Sequence[Decimal], b: Decimal, index: int) -> Decimal:
    """Outcome `index`'s marginal price, the softmax `1 / Σ e^((q_j - q_i)/b)`."""
    with _oracle_context():
        total = sum(((q_j - q[index]) / b).exp() for q_j in q)
        return 1 / total


def _oracle_cost(q: Sequence[Decimal], b: Decimal) -> Decimal:
    """`b·ln(Σ e^(q_j/b))`, unshifted, at 60 digits: beyond the engine's 50, so
    the oracle's own rounding cannot hide an engine error."""
    with _oracle_context():
        return b * sum((q_j / b).exp() for q_j in q).ln()


def _oracle_after(
    q: Sequence[Decimal], *, index: int, side: str, quantity: Decimal
) -> tuple[Decimal, ...]:
    after = list(q)
    with _oracle_context():
        if side == "buy":
            after[index] += quantity
        else:
            after[index] -= quantity
    return tuple(after)


def _oracle_fill_cost(
    q: Sequence[Decimal], b: Decimal, *, index: int, side: str, quantity: Decimal
) -> Decimal:
    """What a fill costs or pays, rounded toward the pool: up on a buy, down
    on a sell. `0.0000` when it rounds to nothing."""
    after = _oracle_after(q, index=index, side=side, quantity=quantity)
    rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    with _oracle_context():
        change = abs(_oracle_cost(after, b) - _oracle_cost(q, b))
        return change.quantize(_TICK, rounding=rounding)


def _stays_within_limit(
    q: Sequence[Decimal],
    b: Decimal,
    *,
    index: int,
    side: str,
    limit: Decimal,
    ticks: int,
) -> bool:
    """Whether the order's own price, after a fill of `ticks` ticks, is still
    on its side of the limit: at or below it for a buy, at or above for a sell.
    """
    after = _oracle_after(q, index=index, side=side, quantity=ticks * _TICK)
    price = _oracle_price(after, b, index)
    if side == "buy":
        return price <= limit
    return price >= limit


def _oracle_fill_quantity(
    q: Sequence[Decimal],
    b: Decimal,
    *,
    index: int,
    side: str,
    limit: Decimal,
    remaining: Decimal,
) -> Decimal:
    """The most whole ticks, at most `remaining`, that keep the order's price
    within its limit. A bisection, so it shares nothing with the closed form.
    """
    most_ticks = int(remaining / _TICK)
    if not _stays_within_limit(q, b, index=index, side=side, limit=limit, ticks=0):
        return Decimal("0.0000")
    if _stays_within_limit(q, b, index=index, side=side, limit=limit, ticks=most_ticks):
        return most_ticks * _TICK
    within, beyond = 0, most_ticks
    while beyond - within > 1:
        middle = (within + beyond) // 2
        if _stays_within_limit(q, b, index=index, side=side, limit=limit, ticks=middle):
            within = middle
        else:
            beyond = middle
    return within * _TICK


def _oracle_is_executable(
    q: Sequence[Decimal],
    b: Decimal,
    *,
    index: int,
    side: str,
    limit: Decimal,
    remaining: Decimal,
) -> bool:
    """The executable rule on oracle figures: some shares, a cost that does not
    round to nothing, and no worse than the limit per share."""
    quantity = _oracle_fill_quantity(
        q, b, index=index, side=side, limit=limit, remaining=remaining
    )
    if quantity == 0:
        return False
    cost = _oracle_fill_cost(q, b, index=index, side=side, quantity=quantity)
    if cost == 0:
        return False
    if side == "buy":
        return cost <= limit * quantity
    return cost >= limit * quantity


# --- building the module's inputs ------------------------------------------
def _q(*shares: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) for value in shares)


def _order(
    seq: int, outcome_index: int, *, side: Side, limit_price: str, remaining: str
) -> RestingOrder:
    return RestingOrder(
        seq=seq,
        outcome_index=outcome_index,
        side=side,
        limit_price=Decimal(limit_price),
        remaining=Decimal(remaining),
    )


def _seqs_and_quantities(fills: Sequence[PlannedFill]) -> list[tuple[int, Decimal]]:
    return [(fill.order.seq, fill.quantity) for fill in fills]


def _is_refused(
    open_orders: Sequence[RestingOrder],
    *,
    outcome_index: int,
    side: Side,
    limit_price: str,
) -> bool:
    try:
        refuse_if_crosses_book(
            open_orders,
            outcome_index=outcome_index,
            side=side,
            limit_price=Decimal(limit_price),
        )
    except OrderCrossesBook:
        return True
    return False


# --- expected fills: the oracle's figures for the test's own fill order ---
def _oracle_fills(
    q: Sequence[Decimal], b: Decimal, orders_in_fill_order: Sequence[RestingOrder]
) -> list[_ExpectedFill]:
    """Each order filled in the order given, each at the `q` the one before
    left. The order is the test's expectation; the figures are the oracle's."""
    current = tuple(q)
    expected: list[_ExpectedFill] = []
    for order in orders_in_fill_order:
        index = order.outcome_index
        side = str(order.side)
        quantity = _oracle_fill_quantity(
            current,
            b,
            index=index,
            side=side,
            limit=order.limit_price,
            remaining=order.remaining,
        )
        magnitude = _oracle_fill_cost(
            current, b, index=index, side=side, quantity=quantity
        )
        after = _oracle_after(current, index=index, side=side, quantity=quantity)
        expected.append(
            _ExpectedFill(
                seq=order.seq, quantity=quantity, magnitude=magnitude, after_q=after
            )
        )
        current = after
    return expected


# --- the books -------------------------------------------------------------
# ADR 0024's "big trade crosses 3 resting orders". A buy has taken p0 to 0.8808
# at q = [200, 0], and three down-pushers wait. Passed in seq order, so a
# cascade that forgot to sort would take seq 1 first; priority takes 3, 1, 2.
_RISING_Q = _q("200", "0")
_RISING_BOOK = [
    _order(1, 1, side=Side.BUY, limit_price="0.31", remaining="5"),  # d = 0.69
    _order(2, 0, side=Side.SELL, limit_price="0.72", remaining="1000"),  # d = 0.72
    _order(3, 0, side=Side.SELL, limit_price="0.68", remaining="5"),  # d = 0.68
]
_RISING_FILL_ORDER = [_RISING_BOOK[2], _RISING_BOOK[0], _RISING_BOOK[1]]

# A sell has taken p0 to 0.1824 at q = [0, 150]. Two up-pushers of different
# kinds, passed least aggressive first: seq 2's u is 1 - 0.55 = 0.45.
_FALLING_Q = _q("0", "150")
_FALLING_BOOK = [
    _order(1, 0, side=Side.BUY, limit_price="0.40", remaining="1000"),  # u = 0.40
    _order(2, 1, side=Side.SELL, limit_price="0.55", remaining="5"),  # u = 0.45
]
_FALLING_FILL_ORDER = [_FALLING_BOOK[1], _FALLING_BOOK[0]]

# p0 is 0.8176 at q = [150, 0]. Seq 1 comes first (d = 0.68) but its last
# 0.0001 costs a whole tick, 0.0001 against a limit of 0.000032, so it cannot
# fill; seq 2 behind it can.
_PASSING_OVER_Q = _q("150", "0")
_PASSING_OVER_BOOK = [
    _order(1, 1, side=Side.BUY, limit_price="0.32", remaining="0.0001"),
    _order(2, 0, side=Side.SELL, limit_price="0.70", remaining="1000"),
]

# p0 is 0.4875 at q = [0, 5]. Seq 1 comes first (u = 0.4882) and its whole
# 0.1569 left costs 0.0766 against a limit of 0.07659858, so it cannot fill.
# Seq 2 (u = 0.4880) fills 0.1990 for 0.0971 and lifts p0 to 0.4880; from
# there seq 1's fill is 0.0801 for 0.0391, inside its 0.03910482. It is passed
# over at the start because it is capped by what is left, not at its limit.
_REVISITED_Q = _q("0", "5")
_REVISITED_BOOK = [
    _order(1, 0, side=Side.BUY, limit_price="0.4882", remaining="0.1569"),
    _order(2, 0, side=Side.BUY, limit_price="0.4880", remaining="1000"),
]


# --- one fill --------------------------------------------------------------
def test_the_contracts_worked_example_fills_20_5465_shares_for_11_8152() -> None:
    """docs/api/ledger-service.md: a buy of outcome 0 at 0.6000 at q = [20, 0],
    b = 100, fills 20.5465 at once for 11.8152, leaving q = [40.5465, 0]."""
    order = _order(1, 0, side=Side.BUY, limit_price="0.6000", remaining="100")

    fill = executable_fill_of(_q("20", "0"), B, order)

    assert fill is not None
    assert (fill.quantity, fill.cost.total, fill.cost.after_q) == (
        Decimal("20.5465"),
        Decimal("-11.8152"),
        _q("40.5465", "0"),
    )


# The buy's exact fill is 26.28919..., which half-up would make 26.2892.
_TO_THE_LIMIT_CASES = [
    pytest.param(
        _q("120", "100"),
        _order(1, 0, side=Side.BUY, limit_price="0.6137", remaining="1000"),
        id="buy_outcome_0",
    ),
    pytest.param(
        _q("120", "100"),
        _order(1, 1, side=Side.SELL, limit_price="0.3863", remaining="1000"),
        id="sell_outcome_1",
    ),
    pytest.param(
        _q("180", "100"),
        _order(1, 0, side=Side.SELL, limit_price="0.6137", remaining="1000"),
        id="sell_outcome_0",
    ),
    pytest.param(
        _q("180", "100"),
        _order(1, 1, side=Side.BUY, limit_price="0.3863", remaining="1000"),
        id="buy_outcome_1",
    ),
]


@pytest.mark.parametrize(("q", "order"), _TO_THE_LIMIT_CASES)
def test_a_fill_moves_its_orders_price_to_the_limit_and_no_further(
    q: tuple[Decimal, ...], order: RestingOrder
) -> None:
    """ADR 0024, "The fill quantity is in closed form": the quantity is the
    most whole ticks that keep the order's own price within its limit, priced
    as an instant trade of that size, signed as the trade route signs it."""
    side = str(order.side)
    expected_quantity = _oracle_fill_quantity(
        q,
        B,
        index=order.outcome_index,
        side=side,
        limit=order.limit_price,
        remaining=order.remaining,
    )
    magnitude = _oracle_fill_cost(
        q, B, index=order.outcome_index, side=side, quantity=expected_quantity
    )
    expected_total = -magnitude if order.side == Side.BUY else magnitude
    expected_after = _oracle_after(
        q, index=order.outcome_index, side=side, quantity=expected_quantity
    )

    fill = executable_fill_of(q, B, order)

    assert fill is not None
    assert (fill.quantity, fill.cost.total, fill.cost.after_q) == (
        expected_quantity,
        expected_total,
        expected_after,
    )


# The same four orders on the other side of their limits.
_PAST_THE_LIMIT_CASES = [
    pytest.param(
        _q("180", "100"),
        _order(1, 0, side=Side.BUY, limit_price="0.6137", remaining="1000"),
        id="buy_outcome_0",
    ),
    pytest.param(
        _q("180", "100"),
        _order(1, 1, side=Side.SELL, limit_price="0.3863", remaining="1000"),
        id="sell_outcome_1",
    ),
    pytest.param(
        _q("120", "100"),
        _order(1, 0, side=Side.SELL, limit_price="0.6137", remaining="1000"),
        id="sell_outcome_0",
    ),
    pytest.param(
        _q("120", "100"),
        _order(1, 1, side=Side.BUY, limit_price="0.3863", remaining="1000"),
        id="buy_outcome_1",
    ),
]


@pytest.mark.parametrize(("q", "order"), _PAST_THE_LIMIT_CASES)
def test_an_order_whose_price_is_past_its_limit_has_nothing_to_fill(
    q: tuple[Decimal, ...], order: RestingOrder
) -> None:
    """The closed form is negative here. It is no shares, not a fill the other
    way."""
    assert fill_quantity_of(q, B, order) == Decimal("0.0000")
    assert executable_fill_of(q, B, order) is None


def test_a_fill_that_floors_to_no_shares_is_not_executable_and_raises_nothing() -> None:
    """After the worked example's fill the exact remainder to the limit is
    0.0000108 shares. The quantity floors it to none rather than rounding it up
    to a tick, and the order is then not executable, with nothing raised."""
    order = _order(1, 0, side=Side.BUY, limit_price="0.6", remaining="100")
    q = _q("40.5465", "0")

    assert fill_quantity_of(q, B, order) == Decimal("0.0000")
    assert executable_fill_of(q, B, order) is None


# Each fill floors to a positive quantity and fails only the per-fill check:
# the buy's 0.0001 costs a whole tick against a limit of 0.00006, and the
# sell's 0.0003 pays 0.0002 against a limit of 0.00021.
_OVER_THE_LIMIT_CASES = [
    pytest.param(
        _q("40.5464", "0"),
        _order(1, 0, side=Side.BUY, limit_price="0.6", remaining="100"),
        id="buy",
    ),
    pytest.param(
        _q("84.7301", "0"),
        _order(1, 0, side=Side.SELL, limit_price="0.7", remaining="100"),
        id="sell",
    ),
]


@pytest.mark.parametrize(("q", "order"), _OVER_THE_LIMIT_CASES)
def test_a_fill_that_would_cost_more_than_its_limit_is_not_executable(
    q: tuple[Decimal, ...], order: RestingOrder
) -> None:
    """The executable rule's per-fill check. The module's quantity is asserted
    positive first, so the refusal is the check's and not the floor's."""
    expected_quantity = _oracle_fill_quantity(
        q,
        B,
        index=order.outcome_index,
        side=str(order.side),
        limit=order.limit_price,
        remaining=order.remaining,
    )
    assert expected_quantity > 0, "precondition: the fill must floor to shares"

    assert fill_quantity_of(q, B, order) == expected_quantity
    assert executable_fill_of(q, B, order) is None


# Both fill 0.0002 shares and move 0.0001 credits, exactly `0.5 × 0.0002`.
_ON_THE_LIMIT_CASES = [
    pytest.param(
        _q("0", "0.0002"),
        _order(1, 0, side=Side.BUY, limit_price="0.5", remaining="100"),
        id="buy",
    ),
    pytest.param(
        _q("0.0002", "0"),
        _order(1, 0, side=Side.SELL, limit_price="0.5", remaining="100"),
        id="sell",
    ),
]


@pytest.mark.parametrize(("q", "order"), _ON_THE_LIMIT_CASES)
def test_a_fill_costing_exactly_its_limit_is_executable(
    q: tuple[Decimal, ...], order: RestingOrder
) -> None:
    """The per-fill check is "at most" and "at least": a fill on the limit is
    taken."""
    side = str(order.side)
    expected_quantity = _oracle_fill_quantity(
        q,
        B,
        index=order.outcome_index,
        side=side,
        limit=order.limit_price,
        remaining=order.remaining,
    )
    expected_magnitude = _oracle_fill_cost(
        q, B, index=order.outcome_index, side=side, quantity=expected_quantity
    )
    assert expected_magnitude == order.limit_price * expected_quantity, (
        "precondition: the case must sit on the limit"
    )

    fill = executable_fill_of(q, B, order)

    assert fill is not None
    assert (fill.quantity, fill.cost.magnitude) == (
        expected_quantity,
        expected_magnitude,
    )


# A remainder of one tick on a saturated book. The buy's cost is so far below
# the engine's last digit that it is exactly zero; the sell's proceeds are
# about 0.000095, which floor to nothing.
_PRICES_TO_NOTHING_CASES = [
    pytest.param(
        _q("0", "12000"),
        _order(1, 0, side=Side.BUY, limit_price="0.5", remaining="0.0001"),
        CostBelowTick,
        id="cost_below_tick",
    ),
    pytest.param(
        _q("300", "0"),
        _order(1, 0, side=Side.SELL, limit_price="0.5", remaining="0.0001"),
        ProceedsBelowTick,
        id="proceeds_below_tick",
    ),
]


@pytest.mark.parametrize(("q", "order", "refusal"), _PRICES_TO_NOTHING_CASES)
def test_a_fill_that_prices_to_nothing_is_not_executable_and_raises_nothing(
    q: tuple[Decimal, ...], order: RestingOrder, refusal: type[Exception]
) -> None:
    """docs/api/ledger-service.md: a placement never returns `cost_below_tick`
    or `proceeds_below_tick`; the order rests. The precondition proves the
    pricing refuses this fill, at the quantity the module would price, so the
    `None` is the module catching it."""
    assert fill_quantity_of(q, B, order) == order.remaining, (
        "precondition: the module must price the whole remainder"
    )
    with pytest.raises(refusal):
        trade_cost_of(
            q,
            B,
            index=order.outcome_index,
            side=order.side,
            quantity=order.remaining,
        )

    assert executable_fill_of(q, B, order) is None


def test_a_fill_is_capped_at_the_orders_remainder() -> None:
    """The worked example's book, with 10 shares left of an order that could
    take 20.5465."""
    q = _q("20", "0")
    order = _order(1, 0, side=Side.BUY, limit_price="0.6000", remaining="10")
    expected_cost = _oracle_fill_cost(q, B, index=0, side="buy", quantity=Decimal("10"))

    fill = executable_fill_of(q, B, order)

    assert fill is not None
    assert (fill.quantity, fill.cost.total, fill.cost.after_q) == (
        Decimal("10"),
        -expected_cost,
        _q("30", "0"),
    )


def test_a_fill_on_a_market_with_three_outcomes_is_refused() -> None:
    """ADR 0024, "Binary markets only, and no crossed book": with three
    outcomes there is no single price to fill to. `market_not_binary` is the
    placement's to raise."""
    order = _order(1, 0, side=Side.BUY, limit_price="0.5", remaining="1")

    with pytest.raises(ValueError, match="two outcomes"):
        fill_quantity_of(_q("1", "2", "3"), B, order)


# A book the ledger should never hold: a sell of 89.7297 shares to its limit
# with 5 outstanding, and a buy that would take `q` past `Numeric(18, 4)`.
_BROKEN_BOOK_CASES = [
    pytest.param(
        _q("5", "0"),
        _order(1, 0, side=Side.SELL, limit_price="0.30", remaining="1000"),
        InsufficientSharesOutstanding,
        id="insufficient_shares_outstanding",
    ),
    pytest.param(
        _q("99999999999999", "99999999999999"),
        _order(1, 0, side=Side.BUY, limit_price="0.60", remaining="1000"),
        QuantityTooLarge,
        id="quantity_too_large",
    ),
]


@pytest.mark.parametrize(("q", "order", "refusal"), _BROKEN_BOOK_CASES)
def test_a_fill_the_book_cannot_hold_raises_rather_than_waiting(
    q: tuple[Decimal, ...], order: RestingOrder, refusal: type[LedgerError]
) -> None:
    """`executable_fill_of`'s docstring: these two mean the book or the order
    is wrong, not that the order waits, so they are not passed over as `None`
    the way a fill that prices to nothing is."""
    with pytest.raises(refusal):
        executable_fill_of(q, B, order)


# --- the cascade -----------------------------------------------------------
def test_a_cascade_with_no_resting_orders_plans_nothing_on_any_market() -> None:
    """Every trade runs the cascade, including a trade on a market with three
    outcomes, which holds no orders and must not be refused for it."""
    assert cascade_of(_q("1", "2", "3"), B, []) == []


def test_a_cascade_with_an_order_on_a_market_with_three_outcomes_is_refused() -> None:
    """`cascade_of`'s docstring: the refusal reaches its caller rather than
    the order being passed over as not executable."""
    order = _order(1, 0, side=Side.BUY, limit_price="0.5", remaining="1")

    with pytest.raises(ValueError, match="two outcomes"):
        cascade_of(_q("1", "2", "3"), B, [order])


def test_one_big_trade_crosses_three_resting_orders_and_fills_them_by_price_then_seq() -> (
    None
):
    """The pure planner's half of ADR 0024's first owed test: the fill order
    (ascending d) and the final price, which lands on the last order's limit.
    The trade-path half, through the ledger, is still owed."""
    expected = _oracle_fills(_RISING_Q, B, _RISING_FILL_ORDER)

    fills = cascade_of(_RISING_Q, B, _RISING_BOOK)

    assert _seqs_and_quantities(fills) == [
        (3, Decimal("5")),
        (1, Decimal("5")),
        (2, expected[2].quantity),
    ]
    assert fills[-1].cost.after_q == expected[-1].after_q
    final_price = _oracle_price(fills[-1].cost.after_q, B, 0)
    assert final_price.quantize(_TICK, rounding=ROUND_HALF_UP) == Decimal("0.7200")


def test_each_fill_is_priced_at_the_q_the_fill_before_it_left() -> None:
    """Each fill costs what an instant trade of its size would at the book the
    previous fill left, not at the book the cascade started from."""
    expected = _oracle_fills(_RISING_Q, B, _RISING_FILL_ORDER)

    fills = cascade_of(_RISING_Q, B, _RISING_BOOK)

    assert [(fill.cost.magnitude, fill.cost.after_q) for fill in fills] == [
        (each.magnitude, each.after_q) for each in expected
    ]


# Two orders on one limit, passed higher seq first so a sort that ignored seq
# would keep seq 7 ahead. Whichever fills first takes p0 to the limit.
_EQUAL_THRESHOLD_CASES = [
    pytest.param(
        _q("150", "0"),
        [
            _order(7, 0, side=Side.SELL, limit_price="0.70", remaining="1000"),
            _order(4, 0, side=Side.SELL, limit_price="0.70", remaining="1000"),
        ],
        id="pushing_down",
    ),
    pytest.param(
        _q("0", "150"),
        [
            _order(7, 0, side=Side.BUY, limit_price="0.30", remaining="1000"),
            _order(4, 0, side=Side.BUY, limit_price="0.30", remaining="1000"),
        ],
        id="pushing_up",
    ),
]


@pytest.mark.parametrize(("q", "orders"), _EQUAL_THRESHOLD_CASES)
def test_equal_thresholds_fill_the_lower_seq_first(
    q: tuple[Decimal, ...], orders: list[RestingOrder]
) -> None:
    """The tie-break of "Priority is price, then an identity `seq`", on both
    sides of the book."""
    expected = _oracle_fills(q, B, [orders[1]])

    fills = cascade_of(q, B, orders)

    assert _seqs_and_quantities(fills) == [(4, expected[0].quantity)]


def test_the_most_aggressive_order_can_take_the_whole_move() -> None:
    """ADR 0024, "This can starve less aggressive orders, and that is on
    purpose": seq 2's lower d fills first and takes p0 past seq 1's limit."""
    q = _q("150", "0")
    orders = [
        _order(1, 0, side=Side.SELL, limit_price="0.70", remaining="1000"),
        _order(2, 0, side=Side.SELL, limit_price="0.68", remaining="1000"),
    ]
    expected = _oracle_fills(q, B, [orders[1]])

    fills = cascade_of(q, B, orders)

    assert _seqs_and_quantities(fills) == [(2, expected[0].quantity)]


def test_a_trade_that_lowers_the_price_fills_up_pushers_by_descending_threshold() -> (
    None
):
    """The up-pushers' half of the priority rule, across both kinds of
    up-pusher: seq 2 (u = 0.45) before seq 1 (u = 0.40), and p0 ends on seq
    1's limit."""
    expected = _oracle_fills(_FALLING_Q, B, _FALLING_FILL_ORDER)

    fills = cascade_of(_FALLING_Q, B, _FALLING_BOOK)

    assert _seqs_and_quantities(fills) == [(2, Decimal("5")), (1, expected[1].quantity)]
    final_price = _oracle_price(fills[-1].cost.after_q, B, 0)
    assert final_price.quantize(_TICK, rounding=ROUND_HALF_UP) == Decimal("0.4000")


def test_the_cascade_passes_over_an_order_it_cannot_fill_and_fills_the_next() -> None:
    """The cascade's decision: an order that is not executable is passed over,
    not a stop."""
    expected = _oracle_fills(_PASSING_OVER_Q, B, [_PASSING_OVER_BOOK[1]])

    fills = cascade_of(_PASSING_OVER_Q, B, _PASSING_OVER_BOOK)

    assert _seqs_and_quantities(fills) == [(2, expected[0].quantity)]


def test_a_fill_that_makes_a_passed_over_order_executable_sends_the_cascade_back_for_it() -> (
    None
):
    """The cascade's decision, "until no waiting order is executable": after
    each fill it looks again from the most aggressive order. Seq 1 is not
    executable at the start and is at the `q` seq 2's fill leaves, so a
    cascade that walked the book once, in priority order, would end after
    seq 2 with seq 1 executable."""
    seq_1, seq_2 = _REVISITED_BOOK
    expected = _oracle_fills(_REVISITED_Q, B, [seq_2, seq_1])
    for q, executable in [(_REVISITED_Q, False), (expected[0].after_q, True)]:
        assert (
            _oracle_is_executable(
                q,
                B,
                index=seq_1.outcome_index,
                side=str(seq_1.side),
                limit=seq_1.limit_price,
                remaining=seq_1.remaining,
            )
            is executable
        ), f"precondition: seq 1 executable at {q} must be {executable}"

    fills = cascade_of(_REVISITED_Q, B, _REVISITED_BOOK)

    assert [(fill.order.seq, fill.quantity, fill.cost.magnitude) for fill in fills] == [
        (each.seq, each.quantity, each.magnitude) for each in expected
    ]


def test_an_order_smaller_than_the_move_fills_once_for_all_of_it() -> None:
    """ADR 0024: an order fills at most once per commit. After its 5 shares p0
    is still 0.81, above the 0.60 limit, so a cascade that kept the order
    waiting would fill it again."""
    q = _q("150", "0")
    order = _order(1, 0, side=Side.SELL, limit_price="0.60", remaining="5")
    after = _q("145", "0")
    assert _oracle_price(after, B, 0) > order.limit_price, (
        "precondition: the order must still be past its limit after the fill"
    )

    fills = cascade_of(q, B, [order])

    assert [(fill.order.seq, fill.quantity, fill.cost.after_q) for fill in fills] == [
        (1, Decimal("5"), after)
    ]


_CASCADE_BOOKS = [
    pytest.param(_RISING_Q, _RISING_BOOK, id="rising"),
    pytest.param(_FALLING_Q, _FALLING_BOOK, id="falling"),
    pytest.param(_PASSING_OVER_Q, _PASSING_OVER_BOOK, id="passing_over"),
]


@pytest.mark.parametrize(("q", "orders"), _CASCADE_BOOKS)
def test_no_order_is_left_executable_after_the_cascade(
    q: tuple[Decimal, ...], orders: list[RestingOrder]
) -> None:
    """ADR 0024's invariant, checked by the oracle: whatever is left of every
    order is not executable at the `q` the cascade ends on."""
    fills = cascade_of(q, B, orders)
    final_q = fills[-1].cost.after_q if fills else q

    for order in orders:
        filled = sum(
            (fill.quantity for fill in fills if fill.order.seq == order.seq),
            Decimal(0),
        )
        left = order.remaining - filled
        if left == 0:
            continue
        assert not _oracle_is_executable(
            final_q,
            B,
            index=order.outcome_index,
            side=str(order.side),
            limit=order.limit_price,
            remaining=left,
        ), f"seq {order.seq} is still executable with {left} left"


# --- the crossing check ----------------------------------------------------
# One row per row of ADR 0024's threshold table. The nearest opposing order is
# listed second, so taking the first would miss it; the bound is in the new
# order's own outcome.
_CROSSING_CASES = [
    pytest.param(
        0,
        Side.BUY,
        "0.65",
        [
            _order(1, 1, side=Side.BUY, limit_price="0.30", remaining="1"),  # d 0.70
            _order(2, 0, side=Side.SELL, limit_price="0.62", remaining="1"),  # d 0.62
        ],
        "0.62",
        id="buy_outcome_0",
    ),
    pytest.param(
        1,
        Side.SELL,
        "0.40",
        [
            _order(1, 1, side=Side.BUY, limit_price="0.30", remaining="1"),  # d 0.70
            _order(2, 0, side=Side.SELL, limit_price="0.58", remaining="1"),  # d 0.58
        ],
        "0.42",
        id="sell_outcome_1",
    ),
    pytest.param(
        0,
        Side.SELL,
        "0.55",
        [
            _order(1, 0, side=Side.BUY, limit_price="0.50", remaining="1"),  # u 0.50
            _order(2, 1, side=Side.SELL, limit_price="0.42", remaining="1"),  # u 0.58
        ],
        "0.58",
        id="sell_outcome_0",
    ),
    pytest.param(
        1,
        Side.BUY,
        "0.45",
        [
            _order(1, 0, side=Side.BUY, limit_price="0.50", remaining="1"),  # u 0.50
            _order(2, 1, side=Side.SELL, limit_price="0.42", remaining="1"),  # u 0.58
        ],
        "0.42",
        id="buy_outcome_1",
    ),
]


@pytest.mark.parametrize(
    ("outcome_index", "side", "limit_price", "open_orders", "bound"),
    _CROSSING_CASES,
)
def test_a_new_order_crossing_the_book_is_refused_with_the_bound_its_limit_must_clear(
    outcome_index: int,
    side: Side,
    limit_price: str,
    open_orders: list[RestingOrder],
    bound: str,
) -> None:
    """The refusal of "A placement that would cross the book is refused,
    `order_crosses_book`", carrying the side and the bound the controller maps
    to `max_limit_price` or `min_limit_price`."""
    with pytest.raises(OrderCrossesBook) as refusal:
        refuse_if_crosses_book(
            open_orders,
            outcome_index=outcome_index,
            side=side,
            limit_price=Decimal(limit_price),
        )

    assert (refusal.value.side, refusal.value.bound) == (side, Decimal(bound))


# The contract's "strictly below" and "strictly above", from both sides.
_ON_THE_BOUND_CASES = [
    pytest.param(
        Side.BUY,
        "0.62",
        _order(1, 0, side=Side.SELL, limit_price="0.62", remaining="1"),
        True,
        id="up_on_bound",
    ),
    pytest.param(
        Side.BUY,
        "0.6199",
        _order(1, 0, side=Side.SELL, limit_price="0.62", remaining="1"),
        False,
        id="up_one_tick_inside",
    ),
    pytest.param(
        Side.SELL,
        "0.58",
        _order(1, 0, side=Side.BUY, limit_price="0.58", remaining="1"),
        True,
        id="down_on_bound",
    ),
    pytest.param(
        Side.SELL,
        "0.5801",
        _order(1, 0, side=Side.BUY, limit_price="0.58", remaining="1"),
        False,
        id="down_one_tick_inside",
    ),
]


@pytest.mark.parametrize(
    ("side", "limit_price", "opposing", "crosses"), _ON_THE_BOUND_CASES
)
def test_a_limit_on_its_bound_crosses_and_one_tick_inside_does_not(
    side: Side, limit_price: str, opposing: RestingOrder, crosses: bool
) -> None:
    """docs/api/ledger-service.md: a buy's limit must be strictly below the
    bound and a sell's strictly above it."""
    refused = _is_refused(
        [opposing], outcome_index=0, side=side, limit_price=limit_price
    )

    assert refused is crosses


# Every open order pushes the way the new one does, so none is in its way,
# however far apart the limits are.
_SAME_WAY_CASES = [
    pytest.param(
        0,
        Side.BUY,
        "0.90",
        [
            _order(1, 0, side=Side.BUY, limit_price="0.50", remaining="1"),  # u 0.50
            _order(2, 1, side=Side.SELL, limit_price="0.20", remaining="1"),  # u 0.80
        ],
        id="up",
    ),
    pytest.param(
        0,
        Side.SELL,
        "0.10",
        [
            _order(1, 0, side=Side.SELL, limit_price="0.60", remaining="1"),  # d 0.60
            _order(2, 1, side=Side.BUY, limit_price="0.30", remaining="1"),  # d 0.70
        ],
        id="down",
    ),
]


@pytest.mark.parametrize(
    ("outcome_index", "side", "limit_price", "open_orders"), _SAME_WAY_CASES
)
def test_orders_pushing_the_same_way_never_cross(
    outcome_index: int,
    side: Side,
    limit_price: str,
    open_orders: list[RestingOrder],
) -> None:
    """Only an order pushing the other way can fire, or be fired by, the new
    one."""
    refused = _is_refused(
        open_orders,
        outcome_index=outcome_index,
        side=side,
        limit_price=limit_price,
    )

    assert refused is False


@pytest.mark.parametrize(
    ("side", "direction"),
    [
        pytest.param(Side.BUY, "below", id="buy"),
        pytest.param(Side.SELL, "above", id="sell"),
    ],
)
def test_the_refusal_is_a_409_order_crosses_book_saying_which_way_the_limit_must_clear(
    side: Side, direction: str
) -> None:
    """docs/api/ledger-service.md promises `409 order_crosses_book`, and the
    status and the code live on the error class, so they are pinned here. The
    message tells a buy to go below the bound and a sell above it; built from
    a `Side`, as `refuse_if_crosses_book` builds it."""
    refusal = OrderCrossesBook(side=side, bound=Decimal("0.62"))

    assert isinstance(refusal, LedgerError)
    assert (refusal.status_code, refusal.code) == (409, "order_crosses_book")
    assert refusal.message.endswith(f"must be {direction} 0.62")
