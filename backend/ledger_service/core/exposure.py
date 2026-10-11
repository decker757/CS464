"""What resolving a market would cost, as arithmetic. [2.2] #6

Pure: no session, no I/O. `service/exposure.py` reads the numbers and asks
this module the question.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal


def has_payout_over_pool(*, payouts: Iterable[Decimal], pool: Decimal) -> bool:
    """True when any outcome's payout is strictly greater than the pool.

    A payout equal to the pool pays out in full with nothing left over, so it
    does not raise the flag. DECISIONS.md, "Exposure flags a payout the pool
    cannot cover, not one above the seed".
    """
    return any(payout > pool for payout in payouts)
