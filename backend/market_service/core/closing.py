"""The ADR 0011 predicate, in one place.

A market stops accepting trades the instant its close time passes — a fact
about two values, not about whether a background sweep has written CLOSED
into the status column yet. Two layers need exactly this comparison:
`service/closing.py::is_open_for_trading`, the authority the trade path and
[2.3] #7's early close ask before they act, and `model/schemas.py`, which
derives the status a trader is shown for the same reason (D-022, ADR 0011's
amendment). Restating the comparison in both was the "three places to get it
wrong" ADR 0011 names by hand, so it holds here instead, in the one package
both are allowed to import: this repository's layering is `controller ->
service -> core/model`, and `model/` may not reach up into `service/`.

Takes `status_is_open` as a `bool` rather than a `MarketStatus` member,
because `core/` may not import `model/entities` either — nothing below
`service` or `model` reaches sideways or up. Comparing a caller's own status
value to OPEN is one line, and it stays with the caller; this module owes
nothing about what a status even is.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.clock import as_utc


def is_open_for_trading(
    status_is_open: bool,
    close_time: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """May a trade execute against a market in this state, right now?

    Two conditions, both load-bearing: the caller's status has to already be
    the open one, which is the administrator's decision that a market should
    be tradeable at all and the one thing the clock cannot supply; and
    `close_time` has to be strictly in the future, which is the clock's half
    and is exact.

    `close_time` is run through `as_utc` before the comparison, so a caller
    holding a naive datetime — a raw driver value, or a hand-built object in
    a test — gets the same UTC assumption this predicate has always applied,
    rather than a `TypeError`. `model/schemas.py` never needs that tolerance,
    because Pydantic has already normalised `close_time` to aware by the time
    it calls this, but this function does not assume that of every caller.
    """
    if not status_is_open:
        return False
    if close_time is None:
        # Unreachable through the market service's API for an OPEN market — it
        # cannot be published without a close time, because
        # `problems_blocking_submission` requires one and `publish` re-runs
        # it. Treated as closed rather than as open forever, because a market
        # with no closing time is a market with no resolution date either,
        # and letting people trade into that is the worse of the two
        # failures.
        return False
    return as_utc(close_time) > (now or datetime.now(UTC))
