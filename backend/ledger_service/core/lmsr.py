"""The LMSR pricing engine. [F-3] #43

`C(q) = b·ln(Σ e^(q_i/b))`, and the marginal prices that are its gradient.

**Why this is `ledger_service/core/` and not `backend/shared/`.** ADR 0005
named the engine as one of three things to extract into `shared/`, and ADR
0012 recorded it as landing there with this issue. It does not, and the
reason is a change those records predate: ADR 0010 settled that the ledger
owns the write path, because the trading composite owns no role and no schema.
A service that holds no cross-schema grant cannot read `q`, so it cannot
evaluate this function — it calls the ledger, which can. That leaves one
caller, and `shared/`'s bar is that *every* caller needs identical behaviour
and a divergence between two copies would be a bug. One caller fails the bar.
Move it to `shared/` on the day a second caller can actually read `q`; until
then a shared module would be indirection with nothing on the other end.
Recorded as an amendment on ADR 0012, which is the record this reverses.

Decimal in, Decimal out, unquantized. The caller quantizes to `Numeric(18, 4)`
at the ledger write; rounding here would let a caller round-trip a profit.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from decimal import Context, Decimal, localcontext

# Derived from what the database stores, not from what the tests happen to
# exercise. `Numeric(18, 4)` is 14 integer digits and 4 fractional, so `q_i`
# reaches 1e14 and `b` is as small as 1e-4 — a `q/b` of 1e18, and a cost whose
# magnitude tracks `max(q)` at 14 significant digits. `cost_to_trade` then
# subtracts two such costs to resolve a difference the ledger stores at 1e-4,
# which needs 18 digits before any margin: the leading digits cancel and what
# survives is the answer. 50 leaves 32 digits of headroom over that worst case,
# absorbing the log-sum-exp shift and the softmax division, and is still small
# enough that `exp`/`ln` are not noticeably slow. Widen `Numeric`'s scale and
# this number has to be revisited with it.
_PRECISION = 50

# At least two. A single-outcome market prices a certainty at 1, and a share
# that costs 1 credit and pays 1 credit is not a market. Two other copies of
# this rule exist and must agree: `market_service/core/opening_prices.py`'s
# `_MIN_PRICED_OUTCOMES` refuses it at creation, and `realtime_service`'s
# `PriceEvent` refuses it on the wire. This is the copy the other two defer to.
_MIN_OUTCOMES = 2


def _engine_context() -> AbstractContextManager[Context]:
    """This module's arithmetic, pinned in full.

    A bare `localcontext()` copies the *caller's* context, so setting `prec`
    alone leaves rounding, traps and the exponent range as whatever the caller
    happened to hold — a caller that had enabled the `Inexact` trap would make
    a price raise instead of returning. A fresh `Context` pins every one of
    them, which is what lets the tests treat the engine as a pure function.

    Scoped rather than global because this service does money arithmetic
    elsewhere (`posting.py` quantizes to `Numeric(18, 4)`), and that must keep
    its own rounding mode undisturbed by whatever the engine needs internally.
    """
    return localcontext(Context(prec=_PRECISION))


def _require_positive_b(b: Decimal) -> None:
    if b <= 0:
        raise ValueError(f"b must be positive, got {b}")


def _require_outcomes(q: Sequence[Decimal]) -> None:
    if len(q) < _MIN_OUTCOMES:
        raise ValueError(f"q must name at least {_MIN_OUTCOMES} outcomes, got {len(q)}")


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
    with _engine_context():
        scaled = [q_i / b for q_i in q]
        return b * _log_sum_exp(scaled)


def cost_to_trade(q: Sequence[Decimal], b: Decimal, delta: Sequence[Decimal]) -> Decimal:
    """`C(q + Δ) − C(q)`. Positive is money owed by the trader, negative is
    proceeds — one function and one sign convention for both buying and
    selling.

    **This answer can be smaller than the ledger can store, and that is the
    caller's problem to solve.** A saturated outcome is genuinely worth almost
    nothing, so a real trade in one prices below `Numeric(18, 4)`'s 0.0001
    tick: 100 shares against `q = [1560, 0]` at `b = 100` costs 0.0000288, and
    a wider spread reaches exactly zero.

    **[T-1] #21 changed what happens next, and this paragraph used to say
    otherwise.** It described the write as `ROUND_HALF_UP`, which made a
    sub-tick trade a share transfer charged 0 credits "in the trader's
    favour". `core/pricing.py::quantize_cost` (D-039) now runs in front of
    `posting._quantize`, rounding a buy's magnitude up and a sell's down, so
    both sides round *against* the trader: the same 100 shares cost 0.0001 to
    buy and pay 0 to sell.

    The engine still does not round it off, because rounding here is the
    round-trip profit the boundary contract exists to prevent. What the engine
    also does not decide is what happens to a sell that floors to zero:
    charging a minimum tick, rounding toward the house and refusing the trade
    were choices about money that belonged where there is a request to
    refuse. #21 is that place, and now makes both halves of the choice —
    `core/pricing.py::refuse_sub_tick_proceeds` refuses it (D-041), rather
    than inheriting a zero-cost trade by default.
    `test_a_sub_tick_trade_is_priced_not_rounded` still pins what this
    function itself does: price the trade honestly and leave rounding and
    refusal to the caller.
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

    The prices sum to 1 in exact arithmetic, and to within `_PRECISION`
    significant digits here — three uniform outcomes each return 0.333…3 at 50
    digits, which sums to 1 − 1e-50 rather than to 1. A caller rendering them
    or putting them on the wire quantizes first and owns whatever rounding
    residue that leaves; the engine does not hand back pre-normalised numbers,
    for the same reason it does not quantize a cost.

    Computed directly as a softmax over `q_i/b` rather than as a numerical
    derivative of `cost`. Deliberately independent of `_log_sum_exp` — no
    shared helper, no call into `cost` — because the central-difference test
    that checks price against cost's gradient only proves anything as long as
    the two are separate implementations. Folding this into `_log_sum_exp` to
    remove the apparent duplication would make that test tautological.
    """
    _require_outcomes(q)
    _require_positive_b(b)
    with _engine_context():
        scaled = [q_i / b for q_i in q]
        top = max(scaled)
        weights = [(x - top).exp() for x in scaled]
        total = sum(weights)
        return [w / total for w in weights]
