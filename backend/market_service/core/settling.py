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


def is_in_dispute_window(*, ends_at: datetime | None, now: datetime) -> bool:
    """May this approval still be sent back? `now < ends_at`, exclusive. [3.3] #11.

    False with no `ends_at`: a null is damage, and fails closed as
    `is_settleable` does. Exclusive, so no instant is both inside the window
    and settleable. ADR 0023.
    """
    if ends_at is None:
        return False
    return as_utc(now) < as_utc(ends_at)


def is_settleable(
    *,
    approved: bool,
    settled: bool,
    ends_at: datetime | None,
    now: datetime,
) -> bool:
    """May this market be paid out, as of `now`?

    A settled market always is, so the flag never goes back to false. An
    approved one is once `now` reaches `ends_at + SETTLEMENT_GAP`, edge
    included, where `ends_at` is the window's end stored at approval. Fails
    closed: any other status, or an approved market with no `ends_at`, is not
    settleable. ADR 0023.
    """
    if settled:
        return True
    if not approved or ends_at is None:
        return False
    return as_utc(now) >= as_utc(ends_at) + SETTLEMENT_GAP
