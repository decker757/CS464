"""The price-history replay. [BE][2.4] #63

DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it": the first point is the book's opening, then one point
per trade, each priced from the `q` every trade so far has built.

Prices are `core/lmsr.py`'s raw Decimals in position order. Rounding them to
the wire's scale is the service's, through `book_prices.priced`, the quantizer
the price frame uses, so a point and a frame for one `state_version` give the
same strings. `outcome_id` is joined on there too.

`window_of` then cuts the replayed points by `from` and `to`, and picks the
carry-in point. DECISIONS.md, "The `from`/`to` window and the carry-in point
are applied in `core`, after the replay of every trade".

Pure. No session, no clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.lmsr import _engine_context, prices
from core.pricing import Side


@dataclass(frozen=True)
class ReplayedTrade:
    """One trade as the replay needs it: where, which way, how much, and when."""

    position: int
    side: Side
    quantity: Decimal
    state_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class PricePoint:
    """The prices the book held at one `state_version`, in position order."""

    state_version: int
    occurred_at: datetime
    prices: tuple[Decimal, ...]


def price_history_of(
    trades: Sequence[ReplayedTrade],
    outcome_count: int,
    b: Decimal,
    opened_at: datetime,
) -> list[PricePoint]:
    """The opening point, then one point per trade, oldest first.

    Raises `ValueError`, naming the expected and the actual value, unless the
    trades' `state_version`s run 1, 2, 3, ... in order. The reason is in
    DECISIONS.md, "The price-history replay refuses trades whose state versions
    do not run 1, 2, 3, ... in order".
    """
    q = [Decimal(0)] * outcome_count
    points = [PricePoint(0, opened_at, tuple(prices(q, b)))]

    for expected, trade in enumerate(trades, start=1):
        if trade.state_version != expected:
            raise ValueError(
                f"expected state_version {expected}, got {trade.state_version}"
            )
        with _engine_context():
            if trade.side is Side.BUY:
                q[trade.position] += trade.quantity
            else:
                q[trade.position] -= trade.quantity
        points.append(
            PricePoint(trade.state_version, trade.occurred_at, tuple(prices(q, b)))
        )
    return points


def window_of(
    points: Sequence[PricePoint],
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[PricePoint]:
    """The points a `from` and a `to` ask for, oldest first. None is no bound.

    Both bounds include a point at exactly that time. When `start` is given,
    the last point strictly before it comes first, at its own `occurred_at`,
    so a chart drawn from `start` begins at the level the market had then.
    Nothing is carried in when no point precedes `start`. `start` later than
    `end` is the route's to refuse. DECISIONS.md, "Price history returns raw
    points, one per trade, filtered by `from` and `to` with a carry-in point;
    nothing is bucketed".

    `start` and `end` share a type, so they are keyword-only (backend/CLAUDE.md:
    "Arguments after the first two are keyword-only (`*,`) when two of them
    share a type — two `uuid.UUID`s passed positionally is a swap waiting to
    happen"). The rule's letter would let one of them be positional; both are
    keyword-only because a swapped pair here silently moves the window.
    """
    carry_in: PricePoint | None = None
    window: list[PricePoint] = []
    for point in points:
        from_start = start is None or point.occurred_at >= start
        until_end = end is None or point.occurred_at <= end
        if not from_start:
            carry_in = point
        elif until_end:
            window.append(point)

    if carry_in is None:
        return window
    return [carry_in, *window]
