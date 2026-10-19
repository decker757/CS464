"""The pure core for limit orders: fills, the cascade and the crossing check.
[T-7] #27, [T-6] #26

ADR 0024, "Resting orders fill against the AMM inside the trade that moved the
price", decides the shape; the decisions under it are in DECISIONS.md: "An
order is executable when its floored fill passes the per-fill limit check; a
resting order is one that is not", "Priority is price, then an identity `seq`
assigned under the book lock", "A placement that would cross the book is
refused, `order_crosses_book`", and "The cascade loops until no waiting order
is executable, and passes over an order it cannot fill".

Only binary markets hold orders. The orders arrive as `RestingOrder`s, built
by the service from its `Order` rows, because `core` cannot import `model`.
`PlannedFill`s are what the cascade would do; writing them is the service's.

Pure. No session, no clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from core.errors import CostBelowTick, OrderCrossesBook, ProceedsBelowTick
from core.lmsr import _engine_context
from core.pricing import QUANTUM, Side, TradeCost, trade_cost_of

# Exactly two, a different fact from the "at least two" of `lmsr._MIN_OUTCOMES`
# and `books.MIN_OUTCOMES`: only a binary market puts every order on one price.
_BINARY_OUTCOMES = 2


@dataclass(frozen=True)
class RestingOrder:
    """The cascade's view of one open order.

    `outcome_index` is the outcome's position in `q`. `seq` identifies the
    order within its market, so a caller maps a `PlannedFill` back by it.
    """

    seq: int
    outcome_index: int
    side: Side
    limit_price: Decimal
    remaining: Decimal


@dataclass(frozen=True)
class PlannedFill:
    """One fill of one order, priced as a trade of that size at its `q`."""

    order: RestingOrder
    quantity: Decimal
    cost: TradeCost


def _is_up_pusher(*, outcome_index: int, side: Side) -> bool:
    """Whether filling this order raises outcome 0's price.

    Buying outcome 0 does, and so does selling outcome 1; the other two lower
    it.
    """
    return (outcome_index == 0) == (side == Side.BUY)


def _threshold_of(*, outcome_index: int, limit_price: Decimal) -> Decimal:
    """The limit as outcome 0's price, so every order sits on one line.

    A limit on outcome 1 is a limit on outcome 0 at `1 - limit_price`.
    """
    if outcome_index == 0:
        return limit_price
    with _engine_context():
        return 1 - limit_price


def _priority_of(order: RestingOrder) -> tuple[Decimal, int]:
    """The sort key that puts the most aggressive order first, then lowest `seq`.

    A price-lowering order is the more aggressive the lower its threshold is,
    since it takes the whole move down to it, and a price-raising one the
    higher. The threshold is negated rather than the sort reversed, so that
    `seq` still breaks a tie upwards.
    """
    # One key puts every up-pusher ahead of every down-pusher, and nothing rests
    # on that: ADR 0024 ranks orders only within a direction ("Price priority,
    # then time"), and `cascade_of` ends because every pass takes an order out
    # of `waiting`, not because of how the two directions sort.
    threshold = _threshold_of(
        outcome_index=order.outcome_index, limit_price=order.limit_price
    )
    if _is_up_pusher(outcome_index=order.outcome_index, side=order.side):
        return (-threshold, order.seq)
    return (threshold, order.seq)


def _is_within_limit(order: RestingOrder, quantity: Decimal, cost: TradeCost) -> bool:
    """Whether the fill costs no more (buy) or pays no less (sell) than its limit.

    The limit is held against the rounded cost, so a tick of rounding can
    break a fill the closed form allowed.
    """
    with _engine_context():
        at_limit = order.limit_price * quantity
    if order.side == Side.BUY:
        return cost.magnitude <= at_limit
    return cost.magnitude >= at_limit


def refuse_if_crosses_book(
    open_orders: Sequence[RestingOrder],
    *,
    outcome_index: int,
    side: Side,
    limit_price: Decimal,
) -> None:
    """Raise `OrderCrossesBook` if a new order would fire, or be fired by, an
    open order.

    Only open orders that push the price the other way count, and a limit equal
    to the nearest of them crosses. The error's `bound` is in the new order's
    own outcome terms.
    """
    new_pushes_up = _is_up_pusher(outcome_index=outcome_index, side=side)
    opposing_thresholds: list[Decimal] = []
    for open_order in open_orders:
        pushes_up = _is_up_pusher(
            outcome_index=open_order.outcome_index, side=open_order.side
        )
        if pushes_up != new_pushes_up:
            opposing_thresholds.append(
                _threshold_of(
                    outcome_index=open_order.outcome_index,
                    limit_price=open_order.limit_price,
                )
            )
    if not opposing_thresholds:
        return

    threshold = _threshold_of(outcome_index=outcome_index, limit_price=limit_price)
    if new_pushes_up:
        nearest = min(opposing_thresholds)
        crosses = threshold >= nearest
    else:
        nearest = max(opposing_thresholds)
        crosses = threshold <= nearest
    if crosses:
        # `1 - x` converts a threshold and a limit alike, so it is its own
        # inverse.
        bound = _threshold_of(outcome_index=outcome_index, limit_price=nearest)
        raise OrderCrossesBook(side=side, bound=bound)


def fill_quantity_of(q: Sequence[Decimal], b: Decimal, order: RestingOrder) -> Decimal:
    """Shares that move the order's own price to its limit, floored to a tick.

    The closed form, ADR 0024's "The fill quantity is in closed form". Capped
    at `order.remaining`; `0.0000` when the price is already past the limit or
    the floor leaves nothing. Raises `ValueError` unless the market has two
    outcomes.

    Expects `0 < order.limit_price < 1`, which the orders table's CHECK holds.
    """
    if len(q) != _BINARY_OUTCOMES:
        raise ValueError(f"a limit order needs two outcomes, got {len(q)}")

    own = order.outcome_index
    other = 1 - own
    with _engine_context():
        limit = order.limit_price
        shares_to_limit = b * (limit / (1 - limit)).ln() - (q[own] - q[other])
        if order.side == Side.SELL:
            shares_to_limit = -shares_to_limit
        floored = shares_to_limit.quantize(QUANTUM, rounding=ROUND_FLOOR)

    if floored <= 0:
        return Decimal("0.0000")
    return min(floored, order.remaining)


def executable_fill_of(
    q: Sequence[Decimal], b: Decimal, order: RestingOrder
) -> PlannedFill | None:
    """The fill `order` would take at `q`, or None if it is not executable.

    A fill that floors to no shares, that prices to nothing, or that fails the
    limit check is not executable. `InsufficientSharesOutstanding` and
    `QuantityTooLarge` from the pricing propagate: they mean the book or the
    order is wrong, not that the order waits.
    """
    quantity = fill_quantity_of(q, b, order)
    # `trade_cost_of` expects its caller to have refused a zero quantity.
    if quantity == 0:
        return None

    try:
        cost = trade_cost_of(
            q, b, index=order.outcome_index, side=order.side, quantity=quantity
        )
    except (CostBelowTick, ProceedsBelowTick):
        return None
    if not _is_within_limit(order, quantity, cost):
        return None
    return PlannedFill(order=order, quantity=quantity, cost=cost)


def cascade_of(
    q: Sequence[Decimal], b: Decimal, orders: Sequence[RestingOrder]
) -> list[PlannedFill]:
    """The fills a trade's resulting `q` sets off, in the order they happen.

    Each fill is priced against the `q` the one before it left, and an order
    that is not executable is passed over, not a stop. No order fills twice.
    Returns `[]` for no orders, on a market of any size.

    Raises `ValueError` from `fill_quantity_of` when there are orders and `q`
    is not binary. `InsufficientSharesOutstanding` and `QuantityTooLarge` from
    `trade_cost_of` pass through `executable_fill_of`.
    """
    waiting = sorted(orders, key=_priority_of)
    current = tuple(q)
    fills: list[PlannedFill] = []
    while True:
        next_fill = None
        for order in waiting:
            next_fill = executable_fill_of(current, b, order)
            if next_fill is not None:
                break
        if next_fill is None:
            return fills
        fills.append(next_fill)
        waiting.remove(next_fill.order)
        current = next_fill.cost.after_q
