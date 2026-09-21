"""Rounding a cost to what the ledger can store. [T-1] #21

`core/lmsr.py` returns an unquantized, signed `Decimal` — positive for a buy,
negative for a sell (D-002). `quantize_cost` is the boundary between that and
`Numeric(18, 4)`: it takes the trade's **unsigned magnitude** and the side, and
rounds it to scale 4 so that the residue always favours the pool. The caller —
`service/preview.py` today, [T-2] #22's trade path tomorrow — applies the sign
afterwards.

**Why a magnitude rather than the engine's signed answer.** The acceptance
criterion is "buy cost rounds ceiling, sell proceeds round floor — the residue
accrues to the pool, never to the trader". `ROUND_CEILING` and `ROUND_FLOOR`
only mean that on a non-negative input: handing a sell's signed (negative)
output to `ROUND_FLOOR` pulls it further from zero, which is a *larger*
magnitude — paying the trader the residue instead of the pool. A negative
argument means a caller passed the signed answer straight through, so it is
refused rather than reinterpreted.

Pure. No session, no clock, no configuration.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

# `Numeric(18, 4)`'s shape, restated rather than imported from
# `model/entities.py::AMOUNT_SCALE`. `model` imports `core.database`, so a
# `core` module importing `model` points the dependency back up a layer and
# closes a cycle. Same trade `market_terms._MIN_OUTCOMES` makes, and the same
# mitigation: `test_the_scale_and_precision_match_the_column` fails if the two
# ever disagree, so the duplication cannot drift silently.
_SCALE = 4
_PRECISION = 18
# One tick, public because `service/preview.py` quantizes prices and
# `average_price` to the same scale and had it written out as a literal.
QUANTUM = Decimal(1).scaleb(-_SCALE)

# The largest magnitude `Numeric(18, 4)` can hold: 14 integer digits and 4
# fractional ones, so `99999999999999.9999`. A cost above this is a cost the
# ledger cannot store, which makes it a cost nothing can charge — see
# `service/preview.py`, which is the layer with a request to refuse (D-040).
MAX_MAGNITUDE = Decimal(10) ** (_PRECISION - _SCALE) - QUANTUM


class Side(StrEnum):
    """The two directions a trade can go. The wire's own values.

    Parsed once at the controller from the query string and passed down as the
    enum, so the service layer never re-validates a string the controller has
    already checked.
    """

    BUY = "buy"
    SELL = "sell"


def quantize_cost(magnitude: Decimal, *, side: Side) -> Decimal:
    """`magnitude`, rounded to scale 4 so the residue favours the pool.

    A buy rounds up — the trader is charged the next whole tick, never less
    than the true cost. A sell rounds down — the trader is paid the tick
    below, never more than the true proceeds. Both leave a residue of less
    than one tick that belongs to the market's pool, which is the side already
    expected to lose money under LMSR.

    Raises `ValueError` on a negative `magnitude` rather than inferring what
    the caller meant — see the module docstring.
    """
    if magnitude < 0:
        raise ValueError(f"magnitude must not be negative, got {magnitude}")

    rounding = ROUND_CEILING if side is Side.BUY else ROUND_FLOOR
    return magnitude.quantize(QUANTUM, rounding=rounding)
