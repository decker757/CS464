"""The authoritative price read. [F-9] #112, D-050.

What a client fetches on opening a page and on every reconnect
(`docs/api/realtime-service.md`). Like the preview, it opens a cold market's
book first, through `service/book_prices.py`. It never gates on status: a
closed market still has a price to render, and the reconnect needs one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.lmsr import prices as lmsr_prices
from service import book_prices
from service.book_prices import PricedOutcome


@dataclass(frozen=True)
class Snapshot:
    """A market's authoritative price, as of this read."""

    market_id: uuid.UUID
    state_version: int
    prices: list[PricedOutcome]
    occurred_at: datetime


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

    state_version = rows[0].state_version
    b = rows[0].liquidity_b
    q = [row.q for row in rows]

    return Snapshot(
        market_id=market_id,
        state_version=state_version,
        prices=book_prices.priced(rows, lmsr_prices(q, b)),
        occurred_at=rows[0].state_changed_at,
    )

