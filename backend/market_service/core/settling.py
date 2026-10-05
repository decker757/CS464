"""When a decided market may be settled, stated once. [3.4] #12, ADR 0019.

In `core/` so `service/` can stamp the answer onto every projection. Takes
`bool`s rather than a `MarketStatus` because `core/` may not import `model/`,
the same shape as `core/closing.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.clock import as_utc

# Settlement opens this long after the dispute window closes. A send-back that
# checks the clock just before the window ends can commit just after it, and a
# settlement reading in between would pay the old winner for good. Not a
# setting: at zero the race returns. Judgement call. ADR 0019.
SETTLEMENT_GAP = timedelta(minutes=5)


def is_settleable(
    *,
    approved: bool,
    settled: bool,
    approved_at: datetime | None,
    window: timedelta,
    now: datetime,
) -> bool:
    """May this market be paid out, as of `now`?

    A settled market always is, whatever the window, so the flag never goes
    back to false. An approved one is once `now` reaches `approved_at + window
    + SETTLEMENT_GAP`, edge included. Fails closed: any other status, or an
    approved market with no `approved_at`, is not settleable.
    """
    if settled:
        return True
    if not approved or approved_at is None:
        return False
    opens_at = as_utc(approved_at) + window + SETTLEMENT_GAP
    return as_utc(now) >= opens_at
