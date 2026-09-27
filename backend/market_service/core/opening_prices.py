"""The two pricing numbers a market can state before anyone has traded. [1.2] #2.

The `q = 0` case only. The LMSR engine needs `q` and lives with it on the
ledger (ADR 0005); do not grow a second copy of that formula here. At `q = 0`
every outcome opens at `1/n`, and the market maker can lose at most `b·ln(n)`.
"""

from __future__ import annotations

import math
from decimal import Decimal

# Below two outcomes both numbers are true and useless, so they read as absent.
# Equal to `validation.MIN_OUTCOMES` by coincidence, not as a shared rule.
_MIN_PRICED_OUTCOMES = 2


def max_platform_loss(b: Decimal | None, outcome_count: int) -> float | None:
    """The most the market maker can lose over this market's life: `b·ln(n)`.

    A ceiling, not an estimate. None when `b` is unset or non-positive, or
    there are too few outcomes, all ordinary states for a draft.
    """
    if b is None or b <= 0 or outcome_count < _MIN_PRICED_OUTCOMES:
        return None
    return float(b) * math.log(outcome_count)


def uniform_initial_price(outcome_count: int) -> float | None:
    """What every outcome costs before anyone has traded: `1/n`, or None below two.

    Not rounded, so the prices still sum to one; formatting is the browser's job.
    """
    if outcome_count < _MIN_PRICED_OUTCOMES:
        return None
    return 1.0 / outcome_count
