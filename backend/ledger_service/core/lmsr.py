"""The LMSR pricing engine. [F-3] #43

`C(q) = b·ln(Σ e^(q_i/b))`, and the marginal prices that are its gradient.
Lives in `ledger_service/core/` rather than `shared/`: ADR 0005 puts it
wherever `q` lives, and ADR 0012's bar for `shared/` — every caller needs
identical behaviour — has exactly one caller today.

Decimal in, Decimal out, unquantized. The caller quantizes to `Numeric(18, 4)`
at the ledger write; rounding here would let a caller round-trip a profit.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext

# Local to this module's own computations, never the global context: this
# service also does money arithmetic elsewhere (posting.py's quantize to
# Numeric(18, 4)), and that must keep using the default context and its
# rounding mode undisturbed by whatever this engine needs internally.
#
# 50 significant digits. The stability tests push q/b to 10,000 (q up to
# 1,000,000 against b as small as 0.0001), and the marginal-price test takes
# a central difference of two costs that can be large (hundreds) to recover a
# slope near zero (h = 0.01) — that subtraction cancels several leading
# digits before the result is compared at a 1e-6 relative tolerance. The
# default context (28 digits) already clears that with room to spare; 50 is
# chosen to keep a wide margin across every path here — including the
# log-sum-exp shift itself and the softmax division in `prices` — without
# being large enough to make `exp`/`ln` noticeably slow.
_PRECISION = 50


def _require_positive_b(b: Decimal) -> None:
    if b <= 0:
        raise ValueError(f"b must be positive, got {b}")


def _require_outcomes(q: Sequence[Decimal]) -> None:
    if not q:
        raise ValueError("q must name at least one outcome")


def _log_sum_exp(scaled: Sequence[Decimal]) -> Decimal:
    """`ln(Σ e^(x_i))`, shifted by `max(x)` so every exponent is `<= 0`.

    A direct `Σ e^(x_i)` overflows past `x_i ≈ 700`; subtracting the max
    first bounds every term to `(0, 1]` and folds the max back in afterwards,
    which is exact because `ln(Σ e^(x_i)) = m + ln(Σ e^(x_i - m))` for any `m`.
    """
    top = max(scaled)
    total = sum((x - top).exp() for x in scaled)
    return top + total.ln()


def cost(q: Sequence[Decimal], b: Decimal) -> Decimal:
    """`C(q) = b·ln(Σ e^(q_i/b))`, the market maker's liability at `q`."""
    _require_outcomes(q)
    _require_positive_b(b)
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        scaled = [q_i / b for q_i in q]
        return b * _log_sum_exp(scaled)


def cost_to_trade(q: Sequence[Decimal], b: Decimal, delta: Sequence[Decimal]) -> Decimal:
    """`C(q + Δ) − C(q)`. Positive is money owed by the trader, negative is
    proceeds — one function and one sign convention for both buying and
    selling."""
    _require_outcomes(q)
    if len(delta) != len(q):
        raise ValueError("delta must name every outcome in q")
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        after = [q_i + d_i for q_i, d_i in zip(q, delta)]
        return cost(after, b) - cost(q, b)


def prices(q: Sequence[Decimal], b: Decimal) -> list[Decimal]:
    """The gradient of `cost`: one marginal price per outcome, summing to 1.

    Computed directly as a softmax over `q_i/b` rather than as a numerical
    derivative of `cost`. Deliberately independent of `_log_sum_exp` — no
    shared helper, no call into `cost` — because the central-difference test
    that checks price against cost's gradient only proves anything as long as
    the two are separate implementations. Folding this into `_log_sum_exp` to
    remove the apparent duplication would make that test tautological.
    """
    _require_outcomes(q)
    _require_positive_b(b)
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        scaled = [q_i / b for q_i in q]
        top = max(scaled)
        weights = [(x - top).exp() for x in scaled]
        total = sum(weights)
        return [w / total for w in weights]
