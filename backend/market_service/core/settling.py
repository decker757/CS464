"""When a decided market may be settled, stated once. [3.4] #12, ADR 0019.

In `core/` so `service/` can stamp the answer onto every projection. Takes a
`bool` rather than a `MarketStatus` because `core/` may not import `model/`,
the same shape as `core/closing.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.clock import as_utc

# Settlement opens this long after the dispute window closes. A send-back that
# checks the clock just before the window ends can commit just after it, and a
# settlement reading in between would pay the old winner for good. Not a
# setting: at zero the race returns. Judgement call. ADR 0019.
SETTLEMENT_GAP = timedelta(minutes=5)


def is_settleable(
    status_is_decided: bool,
    approved_at: datetime | None,
    *,
    window: timedelta,
    now: datetime | None = None,
) -> bool:
    """May this market be paid out, as of `now`?

    True once `now` reaches `approved_at + window + SETTLEMENT_GAP`, edge
    included. Fails closed: a market that is not decided, or has no
    `approved_at`, is never settleable, whatever its `approved_at` says.
    Settled markets count as decided, so the flag never flickers back and a
    market settled with no payout can still be repaired.
    """
    if not status_is_decided or approved_at is None:
        return False
    opens_at = as_utc(approved_at) + window + SETTLEMENT_GAP
    return as_utc(now or datetime.now(UTC)) >= opens_at
