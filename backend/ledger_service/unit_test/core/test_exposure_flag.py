"""The exposure flag, as pure arithmetic. [2.2] #6

`core/exposure.py::has_payout_over_pool(*, payouts, pool) -> bool`: true when
any outcome's payout is strictly greater than the pool. A payout equal to the
pool pays out in full with nothing left, and does not raise the flag.
DECISIONS.md, "Exposure flags a payout the pool cannot cover, not one above
the seed". No session, no database.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

POOL = Decimal("481.3569")
TICK = Decimal("0.0001")
ZERO = Decimal("0.0000")


def _has_payout_over_pool():
    from core.exposure import has_payout_over_pool  # noqa: PLC0415

    return has_payout_over_pool


@pytest.mark.parametrize(
    ("payouts", "expected"),
    [
        pytest.param([POOL, ZERO], False, id="payout_equals_pool"),
        pytest.param([POOL + TICK, ZERO], True, id="payout_one_tick_over_pool"),
        pytest.param([ZERO, POOL + TICK], True, id="shortfall_on_a_later_outcome"),
    ],
)
def test_the_flag_is_strictly_greater_than_on_any_outcome(
    payouts: list[Decimal], expected: bool
) -> None:
    """Catches `>` written as `>=` (the first two cases) and a check of only
    the first outcome's payout (the third)."""
    assert _has_payout_over_pool()(payouts=payouts, pool=POOL) is expected
