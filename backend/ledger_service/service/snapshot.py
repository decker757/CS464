"""The authoritative price read. [F-9] #112, D-050.

What a client fetches on opening a page and on every reconnect
(`docs/api/realtime-service.md`). Like the preview, it opens a cold market's
book first, through `service/book_prices.py`. It never gates on status: a
closed market still has a price to render, and the reconnect needs one.

`list_snapshots` is the same price for a list of cards ([X-1] #432), and
opens no book. Beside each price it carries the market's prices a week ago
([X-1] #446).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketBookIncomplete
from core.lmsr import prices as lmsr_prices
from service import book_prices, price_history
from service.book_prices import PricedOutcome

logger = logging.getLogger(__name__)

# How far back a card's price change is measured from. DECISIONS.md, "A card's
# price change is measured over seven days".
_PRICE_CHANGE_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class Snapshot:
    """A market's authoritative price, as of this read."""

    market_id: uuid.UUID
    state_version: int
    prices: list[PricedOutcome]
    occurred_at: datetime


@dataclass(frozen=True)
class ListedSnapshot(Snapshot):
    """A market's price as the batch snapshot answers it, with each outcome's
    price seven days before the read, or None when the market had no trade by
    then."""

    prices_7d_ago: list[PricedOutcome] | None


def _snapshot_of(market_id: uuid.UUID, rows: Sequence[Row]) -> Snapshot:
    """A market's snapshot from its book rows, one per outcome in position
    order, as `book_prices` reads them."""
    q = [row.q for row in rows]
    return Snapshot(
        market_id=market_id,
        state_version=rows[0].state_version,
        prices=book_prices.priced(rows, lmsr_prices(q, rows[0].liquidity_b)),
        occurred_at=rows[0].state_changed_at,
    )


async def snapshot(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> Snapshot:
    """This market's current price. Opens the book first if nobody has yet,
    forwarding `access_token` unchanged.

    On a cold market that first touch commits, so call it with nothing
    pending on the session.
    """
    rows = await book_prices.read_or_open(
        session, market_id, access_token=access_token, terms_client=terms_client
    )
    return _snapshot_of(market_id, rows)


async def list_snapshots(
    session: AsyncSession, market_ids: Sequence[uuid.UUID], *, now: datetime
) -> list[ListedSnapshot]:
    """The current price of each market in `market_ids` that has a book here,
    once each, in the order its id first appears, with its prices seven days
    before `now`. [X-1] #432, #446.

    One statement reads every book and one more their trades, with no lock.
    Opens no book, asks market_service nothing and writes nothing: an id with
    no book is absent (DECISIONS.md, "The batch snapshot opens no book: a
    market without one is absent"), and so is a book that cannot be priced,
    which is logged.
    """
    rows_by_market: dict[uuid.UUID, list[Row]] = {}
    for row in await book_prices.list_book_outcomes_for_markets(session, market_ids):
        rows_by_market.setdefault(row.market_id, []).append(row)

    priceable: dict[uuid.UUID, list[Row]] = {}
    for market_id in dict.fromkeys(market_ids):
        rows = rows_by_market.get(market_id)
        if rows is None:
            continue
        # DECISIONS.md, "A book the batch snapshot cannot price is left out
        # and logged": one damaged book must not blank the whole list.
        try:
            book_prices.refuse_unpriceable(len(rows), rows[0].liquidity_b)
        except MarketBookIncomplete:
            logger.warning(
                "left market %s out of a batch snapshot: its book cannot be "
                "priced",
                market_id,
            )
            continue
        priceable[market_id] = rows

    prices_then = await price_history.list_prices_at(
        session, priceable, at=now - _PRICE_CHANGE_WINDOW
    )
    listed = []
    for market_id, rows in priceable.items():
        snapshot = _snapshot_of(market_id, rows)
        listed.append(
            ListedSnapshot(**vars(snapshot), prices_7d_ago=prices_then.get(market_id))
        )
    return listed

