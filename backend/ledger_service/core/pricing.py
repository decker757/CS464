"""Rounding a cost to what the ledger can store. [T-1] #21

`core/lmsr.py` returns an unquantized, signed `Decimal` — positive for a buy,
negative for a sell (D-002). `quantize_cost` is the boundary between that and
`Numeric(18, 4)`: it takes the trade's **unsigned magnitude** and the side, and
rounds it to scale 4 so that the residue always favours the pool. The caller —
`trade_cost_of`, for both the preview and the trade — applies the sign
afterwards.

**Why a magnitude rather than the engine's signed answer.** The acceptance
criterion is "buy cost rounds ceiling, sell proceeds round floor — the residue
accrues to the pool, never to the trader". `ROUND_CEILING` and `ROUND_FLOOR`
only mean that on a non-negative input: handing a sell's signed (negative)
output to `ROUND_FLOOR` pulls it further from zero, which is a *larger*
magnitude — paying the trader the residue instead of the pool. A negative
argument means a caller passed the signed answer straight through, so it is
refused rather than reinterpreted.

`quantize_cost` refuses a result of `0.0000`: `proceeds_below_tick` on a
sell, `cost_below_tick` on a buy. Taking real shares for zero credits is the
surprise this ticket exists to prevent (D-041). The refusal is inside the
rounding rather than beside it because [T-2] #22's write path has to make the
same refusal, and a separate function is one a caller can forget to call.

Pure. No session, no clock, no configuration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum

from core.errors import (
    CostBelowTick,
    InsufficientSharesOutstanding,
    ProceedsBelowTick,
    QuantityTooLarge,
)
from core.lmsr import _engine_context, cost_to_trade

# `Numeric(18, 4)`'s shape, restated rather than imported from
# `model/entities.py::AMOUNT_SCALE`. `model` imports `core.database`, so a
# `core` module importing `model` points the dependency back up a layer and
# closes a cycle. Same trade `books.MIN_OUTCOMES` makes, and the same
# mitigation: `test_the_scale_and_precision_match_the_column` fails if the two
# ever disagree, so the duplication cannot drift silently.
_SCALE = 4
_PRECISION = 18
# One tick. Public because `quantize_price` and `quantize_cost` below are
# both defined in terms of it and callers compare against it; the scale it
# encodes is the column's, restated above.
QUANTUM = Decimal(1).scaleb(-_SCALE)

# The largest magnitude `Numeric(18, 4)` can hold: 14 integer digits and 4
# fractional ones, so `99999999999999.9999`. A cost above this is a cost the
# ledger cannot store, which makes it a cost nothing can charge — see
# `trade_cost_of` (D-040).
MAX_MAGNITUDE = Decimal(10) ** (_PRECISION - _SCALE) - QUANTUM


def quantize_price(price: Decimal) -> Decimal:
    """An outcome's price, at the wire's scale 4, `ROUND_HALF_UP`.

    The one rounding rule for every price this service publishes: the
    preview's `prices`, the snapshot's, and [T-2] #22's `PriceEvent`. Both
    docs pages promise a client the same string from each for the same state,
    and that holds because they all call this, not because copies agree.

    Half-up rather than `quantize_cost`'s directional rounding, because a
    price is display and nobody is charged it — there is no side for the
    residue to favour.
    """
    return price.quantize(QUANTUM, rounding=ROUND_HALF_UP)


class Side(StrEnum):
    """The two directions a trade can go. The wire's own values.

    Parsed once at the controller from the query string and passed down as the
    enum, so the service layer never re-validates a string the controller has
    already checked.
    """

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class TradeCost:
    """What one trade costs, and the `q` it leaves behind."""

    after_q: tuple[Decimal, ...]
    magnitude: Decimal  # unsigned, rounded toward the pool
    total: Decimal  # signed for the trader: negative on a buy


def quantize_cost(magnitude: Decimal, *, side: Side | str) -> Decimal:
    """`magnitude`, rounded to scale 4 so the residue favours the pool.

    A buy rounds up and a sell rounds down, so the residue of rounding
    `magnitude` belongs to the market's pool, which is the side already
    expected to lose money under LMSR. That is a guarantee about
    `magnitude`, not about the true cost: the engine's answer can be off by
    one unit in its 50th significant digit — a relative error, whose size
    scales with the cost — so a cost exactly on a tick can be charged one
    tick over, and a sell's proceeds a hair under a tick can be paid the
    whole tick, the one case the residue runs toward the trader. What is
    bounded is the quoted error after rounding: one tick, plus the engine's
    own last-digit residue. Not one tick flat — at `q = [1315, 1000]`,
    `b = 3`, a buy of `0.0001` has a true cost of `0.0001 - 2.5e-50`, so the
    correct ceiling is one tick and the quote is two, an error of one tick
    *and* that residue. The overshoot is 46 orders of magnitude below the
    tick it overshoots, which is why this is a statement about the bound's
    shape rather than a reason to change the rounding. D-044,
    "A cost exactly on a tick can round one tick against the trader, or
    toward them on a sell", has the cases and why precision cannot remove
    them.

    A result of `0.0000` is refused on both sides — `ProceedsBelowTick` on a
    sell, `CostBelowTick` on a buy — because a magnitude reaching this
    function is the price of a real quantity. A buy reaches zero only when
    the engine returned exactly zero, which it does past about 110·b of skew.
    The caller must refuse a quantity of zero before pricing it, or it is
    refused here under a code that blames the price.

    `side` is coerced, because `Side` is a `StrEnum` and `"buy" is Side.BUY`
    is False: an unconverted string would fall into the floor branch.

    Raises `ValueError` on a negative `magnitude` rather than inferring what
    the caller meant — see the module docstring.
    """
    side = Side(side)
    if magnitude < 0:
        raise ValueError(f"magnitude must not be negative, got {magnitude}")

    rounding = ROUND_CEILING if side is Side.BUY else ROUND_FLOOR
    quantized = magnitude.quantize(QUANTUM, rounding=rounding)
    if quantized == 0:
        raise CostBelowTick if side is Side.BUY else ProceedsBelowTick
    return quantized


def trade_cost_of(
    q: Sequence[Decimal],
    b: Decimal,
    *,
    index: int,
    side: Side | str,
    quantity: Decimal,
) -> TradeCost:
    """Price `quantity` of outcome `index` at `q`, `b`.

    The one pricing sequence the preview and the trade share, so the two
    cannot disagree about what a trade costs. Raises
    `InsufficientSharesOutstanding` for a sell above `q[index]`,
    `QuantityTooLarge` past `Numeric(18, 4)` on the new `q` or the cost
    (D-040), and `CostBelowTick`/`ProceedsBelowTick` via `quantize_cost`
    (D-041).

    `side` is coerced, as in `quantize_cost`: `"sell" is Side.SELL` is False,
    so an unconverted string would be priced as a buy.
    """
    side = Side(side)
    if side is Side.SELL and quantity > q[index]:
        raise InsufficientSharesOutstanding

    delta = [Decimal(0)] * len(q)
    delta[index] = quantity if side is Side.BUY else -quantity

    with _engine_context():
        after_q = tuple(q_i + d_i for q_i, d_i in zip(q, delta))

    # D-040: checked before quantizing, which raises `InvalidOperation` past
    # the ambient 28 digits.
    if after_q[index] > MAX_MAGNITUDE:
        raise QuantityTooLarge

    # `copy_abs`, never `abs` (D-042): `abs` rounds at the ambient precision.
    raw = cost_to_trade(q, b, delta).copy_abs()
    if raw > MAX_MAGNITUDE:
        raise QuantityTooLarge

    magnitude = quantize_cost(raw, side=side)
    total = -magnitude if side is Side.BUY else magnitude
    return TradeCost(after_q=after_q, magnitude=magnitude, total=total)


def liquidation_values_of(
    q: Sequence[Decimal], b: Decimal, holdings: Sequence[Decimal]
) -> list[Decimal]:
    """What selling every held outcome would pay, one at a time. [T-4] #24

    "A user's holdings in one market are valued as one sequence of sales"
    (ADR 0018): `holdings[i]` of outcome `i` is sold through `trade_cost_of`,
    in the order given, each against the `q` the previous sale left.
    `0.0000` where nothing is held. A sale whose proceeds round to nothing is
    valued `0.0000` and leaves `q` unchanged for the next sale, because the
    trade route would have refused that sale rather than move the book for
    it. `InsufficientSharesOutstanding` propagates rather than being valued
    at anything — a holding above `q` is the caller's data being wrong, not a
    sale to skip.

    Pure. No session, no clock.
    """
    current = list(q)
    values: list[Decimal] = []
    for index, held in enumerate(holdings):
        if held == 0:
            values.append(Decimal("0.0000"))
            continue
        try:
            cost = trade_cost_of(
                current, b, index=index, side=Side.SELL, quantity=held
            )
        except ProceedsBelowTick:
            values.append(Decimal("0.0000"))
            continue
        values.append(cost.magnitude)
        current = list(cost.after_q)
    return values


def per_share(amount: Decimal, quantity: Decimal) -> Decimal:
    """`amount / quantity`, at a price's scale (D-052).

    The preview's `average_price`: `cost.magnitude / quantity`, divided in
    the engine's pinned context so the ambient precision and its traps never
    reach it, then rounded through `quantize_price` rather than
    `quantize_cost` — an average price is display, not a charge, so there is
    no side for a residue to favour.

    Raises `ValueError` for `quantity <= 0` rather than dividing by it; a
    caller reaches this only after pricing a positive quantity.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    with _engine_context():
        return quantize_price(amount / quantity)


def release_basis(
    basis: Decimal, held: Decimal, quantity: Decimal
) -> tuple[Decimal, Decimal]:
    """A sell's `(released, remaining)` cost basis, at scale 4. [T-3] #23

    The remaining basis is `basis * (held - quantity) / held`, computed at the
    engine's precision and rounded half-up; the released basis is the rest, so
    the two always sum to `basis`, and a full exit leaves exactly zero. Why
    each of those: "A sell releases cost basis at average cost" in DECISIONS.md.

    Raises `ValueError` unless `0 < quantity <= held`. The holding check
    refuses a larger sell long before this is reached.
    """
    if not 0 < quantity <= held:
        raise ValueError(f"quantity must be in (0, {held}], got {quantity}")
    if quantity == held:
        return basis, Decimal(0).quantize(QUANTUM)

    with _engine_context():
        remaining = (basis * (held - quantity) / held).quantize(
            QUANTUM, rounding=ROUND_HALF_UP
        )
        released = basis - remaining
    return released, remaining
