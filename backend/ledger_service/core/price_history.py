"""The price-history replay. [BE][2.4] #63

DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it": the first point is the book's opening, then one point
per trade, each priced from the `q` every trade so far has built.

Prices are `core/lmsr.py`'s raw Decimals in position order. Rounding them to
the wire's scale is the route's, through `book_prices.priced`, the quantizer
the price frame uses, so a point and a frame for one `state_version` give the
same strings. `outcome_id` is joined on there too.

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
