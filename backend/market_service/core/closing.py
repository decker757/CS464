"""The ADR 0011 predicate, stated once. D-023.

In `core/` so both `service/closing.py` and `model/` can import it. Takes a
`bool` rather than a `MarketStatus` because `core/` may not import `model/`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.clock import as_utc


def trading_is_open(
    status_is_open: bool,
    close_time: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """May a trade execute against a market in this state, right now?

    Deliberately not named `is_open_for_trading`: with one name for both, a
    caller holding a `Market` could pass it here, `bool(market)` is always
    True, and a closed market would read open with nothing raised. A naive
    `close_time` is taken as UTC rather than raising.
    """
    if not status_is_open:
        return False
    if close_time is None:
        # Unreachable for an OPEN market, since publishing requires a close
        # time. Closed rather than open forever: the safer failure.
        return False
    return as_utc(close_time) > (now or datetime.now(UTC))
