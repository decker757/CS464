"""The LMSR pricing engine. [F-3] #43

`C(q) = b·ln(Σ e^(q_i/b))`, and the marginal prices that are its gradient.

Here and not in `backend/shared/`: only the ledger can read `q`, so there is
one caller (D-001, ADR 0012's amendment). Move it when a second caller can.

Decimal in, Decimal out, unquantized (D-002). The caller quantizes to
`Numeric(18, 4)` at the ledger write; rounding here would let a caller
round-trip a profit.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from decimal import Context, Decimal, localcontext

# D-003. Sized from `Numeric(18, 4)`: `cost_to_trade` subtracts two costs of
# up to 14 integer digits to resolve a 1e-4 difference, which needs 18 digits
# before any margin. 50 leaves 32 of headroom. Widen `Numeric`'s scale and this
# has to be revisited with it.
_PRECISION = 50

# At least two: one outcome prices a certainty at 1, which is not a market.
# Two other copies must agree: `market_service/core/opening_prices.py`'s
# `_MIN_PRICED_OUTCOMES` and `realtime_service`'s `PriceEvent`.
_MIN_OUTCOMES = 2


def _engine_context() -> AbstractContextManager[Context]:
    """This module's arithmetic, pinned in full and scoped (D-003).

    A fresh `Context`, not a bare `localcontext()`, which would inherit the
    caller's rounding, traps and exponent range: an `Inexact` trap there would
    make a price raise.
    """
    return localcontext(Context(prec=_PRECISION))


def _require_positive_b(b: Decimal) -> None:
    if b <= 0:
        raise ValueError(f"b must be positive, got {b}")


def _require_outcomes(q: Sequence[Decimal]) -> None:
    if len(q) < _MIN_OUTCOMES:
        raise ValueError(f"q must name at least {_MIN_OUTCOMES} outcomes, got {len(q)}")


def _log_sum_exp(scaled: Sequence[Decimal]) -> Decimal:
    """`ln(Σ e^(x_i))`, shifted by `max(x)` so no exponent overflows (D-004)."""
    top = max(scaled)
    total = sum((x - top).exp() for x in scaled)
    return top + total.ln()


def cost(q: Sequence[Decimal], b: Decimal) -> Decimal:
    """`C(q) = b·ln(Σ e^(q_i/b))`, the market maker's liability at `q`."""
    _require_outcomes(q)
    _require_positive_b(b)
    with _engine_context():
        scaled = [q_i / b for q_i in q]
        return b * _log_sum_exp(scaled)


def cost_to_trade(q: Sequence[Decimal], b: Decimal, delta: Sequence[Decimal]) -> Decimal:
    """`C(q + Δ) − C(q)`: positive is owed by the trader, negative is proceeds.

    Unrounded, and it can fall below the ledger's 0.0001 tick: a saturated
    outcome is worth almost nothing. Rounding against the trader and refusing
    a sub-tick trade are the caller's, in `core/pricing.py::quantize_cost`
    (D-039, D-041).
    """
    _require_outcomes(q)
    _require_positive_b(b)
    if len(delta) != len(q):
        raise ValueError("delta must name every outcome in q")
    with _engine_context():
        after = [q_i + d_i for q_i, d_i in zip(q, delta)]
        return cost(after, b) - cost(q, b)


def prices(q: Sequence[Decimal], b: Decimal) -> list[Decimal]:
    """The gradient of `cost`: one marginal price per outcome.

    They sum to 1 within `_PRECISION` digits, not exactly; a caller quantizes
    and owns the residue. A softmax deliberately independent of `cost` and
    `_log_sum_exp` (D-005): merging them would make the gradient test
    tautological.
    """
    _require_outcomes(q)
    _require_positive_b(b)
    with _engine_context():
        scaled = [q_i / b for q_i in q]
        top = max(scaled)
        weights = [(x - top).exp() for x in scaled]
        total = sum(weights)
        return [w / total for w in weights]
