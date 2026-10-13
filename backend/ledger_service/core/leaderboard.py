"""When a stored leaderboard ranking counts as stale. [L-1] #38, [L-3] #40

DECISIONS.md, "A refresh's `computed_at` is Python's clock, read before the
valuation; `stale` is derived on read against the same clock". No database.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def is_stale(computed_at: datetime, *, now: datetime, refresh_seconds: int) -> bool:
    """Whether the ranking is more than two refresh intervals old.

    Exactly twice the interval is still fresh. Tied to the interval rather
    than a constant, so changing the setting keeps it right.
    """
    return now - computed_at > timedelta(seconds=2 * refresh_seconds)
