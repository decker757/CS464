"""The two pricing numbers a market can state before anyone has traded. [1.2] #2.

This is **not** the LMSR engine. The engine is
`C(q) = b·ln(Σ e^(q_i/b))`, it needs the share quantity vector `q`, and per
ADR 0005 it lives with `q` on the trading side. This service never learns `q`
and must never grow a second, divergent copy of that formula — if you are about
to add one here, read `docs/adr/0005-trading-service-boundary.md` first.

What is here is the `q = 0` case, evaluated at the only moment this service
cares about: creation, before a single share exists. There the general formula
collapses to arithmetic that needs no engine at all.

- Every outcome is equally likely, so each opens at `1/n`.
- The worst the automated market maker can ever do is `b·ln(n)`, which is the
  number an administrator is really deciding when they choose `b`.

Both are pure functions of their arguments, with no session, no clock and no
configuration, so the admin's form can be tested without any of those.
"""

from __future__ import annotations

import math
from decimal import Decimal

# Below two outcomes neither number means anything. `ln(1)` is 0, so a
# one-outcome market would advertise that the platform cannot lose, and `1/1`
# would price a certainty at 100%. Both are true and both are useless, and a
# half-typed form hits this constantly, so they are reported as absent instead.
#
# `service.validation.MIN_OUTCOMES` is the same number for a different reason —
# that a market with one outcome is not a market. The agreement is a
# coincidence rather than a shared rule, and `core` cannot import from
# `service` in any case.
_MIN_PRICED_OUTCOMES = 2


def max_platform_loss(b: Decimal | None, outcome_count: int) -> float | None:
    """The most the market maker can lose across the life of this market.

    `b·ln(n)`, and it is a ceiling rather than an estimate: no sequence of
    trades can take more than this out of the platform. That makes it the
    honest thing to show beside the seed subsidy, which is the money put up to
    cover it.

    Returns `None` when `b` is unset or non-positive, or there are too few
    outcomes to price — all of which are ordinary states for a draft.
    """
    if b is None or b <= 0 or outcome_count < _MIN_PRICED_OUTCOMES:
        return None
    return float(b) * math.log(outcome_count)


def uniform_initial_price(outcome_count: int) -> float | None:
    """What every outcome costs before anyone has traded.

    `1/n`. Deliberately not rounded: three outcomes give 0.333… each, and
    rounding here would hand the frontend three prices that sum to 0.9999 and
    look like a bug in the maths rather than a display choice. Formatting is
    the browser's job; summing to one is this function's.

    Returns `None` below two outcomes, for the reason above.
    """
    if outcome_count < _MIN_PRICED_OUTCOMES:
        return None
    return 1.0 / outcome_count
