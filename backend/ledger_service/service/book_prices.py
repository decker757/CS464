"""The price read the preview and the snapshot share. Review of #110.

`service/preview.py` and `service/snapshot.py` each had their own copy of this
read and of the quantizer after it. The two reads differed by one selected
column, the quantizers were identical apart from which dataclass they built,
and the two dataclasses had identical fields. Both docs pages promise that the
two endpoints return the same strings for the same state, and two copies could
only keep that promise by staying in step by hand. This module keeps it by
being the only copy.

**The read** (D-013) is one statement joining `market_books` to
`market_outcomes`, so `q`, `b`, `state_version` and `state_changed_at` come
back under one snapshot. Under READ COMMITTED two statements fail in the
dangerous direction: a trade committing between them hands back a
`state_version` newer than the `q` that was priced. No `with_for_update()`:
D-012, as corrected by D-036 — this read decides no write, so it takes no lock.

**The cold path** is the same in both callers, so it lives here too. If the
read comes back empty, `books.ensure_open` opens and funds the book, taking
the handoff's own locks internally, and the read runs again (D-037). Every
request after a market's first is the single read and writes nothing.

**A second empty read is `MarketBookIncomplete`**, not an `IndexError`. The
join reads a book with no outcome rows as no book, so the cold path finds the
book, returns, and the read comes back empty a second time.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketBookIncomplete
from core.pricing import quantize_price
from model.entities import MarketBook, MarketOutcome
from service import books


@dataclass(frozen=True)
class PricedOutcome:
    """One outcome's price, in `PriceEvent`'s shape (`realtime_service`'s
    `OutcomePrice`): an id, its position, and a price quantized for the wire.
    """

    outcome_id: uuid.UUID
    position: int
    price: Decimal


async def read_or_open(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Sequence[Row]:
    """This market's book, one row per outcome, ordered by position. Opens the
    book first if nobody has yet.

    Every row carries `state_version`, `liquidity_b`, `state_changed_at`,
    `outcome_id`, `position` and `q`. Never empty: an empty read after the
    cold path raises `MarketBookIncomplete`.

    `access_token` is the caller's own, forwarded to `books.ensure_open`
    unchanged. Nothing is minted here.
    """
    rows = await _read(session, market_id)
    if rows:
        return rows

    await books.ensure_open(
        session, market_id, access_token=access_token, transport=transport
    )
    rows = await _read(session, market_id)
    if not rows:
        raise MarketBookIncomplete
    return rows


def priced(rows: Sequence[Row], raw: list[Decimal]) -> list[PricedOutcome]:
    """Zips `raw` back onto the ids and positions `read_or_open` ordered,
    each quantized by `core/pricing.py::quantize_price`."""
    return [
        PricedOutcome(
            outcome_id=row.outcome_id,
            position=row.position,
            price=quantize_price(price),
        )
        for row, price in zip(rows, raw)
    ]


async def _read(session: AsyncSession, market_id: uuid.UUID) -> Sequence[Row]:
    stmt = (
        select(
            MarketBook.state_version,
            MarketBook.liquidity_b,
            MarketBook.state_changed_at,
            MarketOutcome.outcome_id,
            MarketOutcome.position,
            MarketOutcome.q,
        )
        .join(MarketOutcome, MarketOutcome.market_id == MarketBook.market_id)
        .where(MarketBook.market_id == market_id)
        .order_by(MarketOutcome.position)
    )
    return (await session.execute(stmt)).all()
