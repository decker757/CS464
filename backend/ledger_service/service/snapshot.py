"""The authoritative price read. [F-9] #112.

What a client renders when it opens a page, and what it re-fetches on every
reconnect — `docs/api/realtime-service.md`'s "Opening a page" and
"Reconnecting" sequences both start here.

**A second first-toucher, like the preview beside it (D-037).** A market
nobody has touched has no book on this service; this is a read with one
exception, exactly `service/preview.py::quote`'s shape — on a cold market it
opens and funds the book via `service/books.py::ensure_open`, forwarding the
caller's own token, before it can price anything. Every request after the
first, for that market, is a single indexed read that writes nothing.

**It never gates on status.** ADR 0017 says so outright: "the realtime
snapshot serves a closed market's prices." A closed market still has a price
to render — the last one anybody traded at — and the reconnect sequence needs
this route to return a number or the client has nothing to resume its version
check from. Gating would put an error exactly where a price belongs, on the
one read [X-4] #37's reconnect depends on. `service/market_status.py` is
never called here; that hop belongs to the trade path, which fires once per
trade, not to a read that fires on every page open and every reconnect.

The pricing read itself (D-012, D-036) is one joined statement over
`market_books` and `market_outcomes` (D-013), the same statement
`service/preview.py::_read_book` already uses, with no lock — this read
decides no write. `books.ensure_open` takes the handoff's own locks
internally on the cold path, same as it does for the preview.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from core.lmsr import prices as lmsr_prices
from core.pricing import QUANTUM
from model.entities import MarketBook, MarketOutcome
from service import books


@dataclass(frozen=True)
class OutcomeSnapshot:
    """One outcome's price, in `PriceEvent`'s shape."""

    outcome_id: uuid.UUID
    position: int
    price: Decimal


@dataclass(frozen=True)
class Snapshot:
    """A market's authoritative price, as of this read."""

    market_id: uuid.UUID
    state_version: int
    prices: list[OutcomeSnapshot]
    occurred_at: datetime


async def snapshot(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Snapshot:
    """This market's current price. Opens the book first if nobody has yet.

    `access_token` is the caller's own, forwarded to `books.ensure_open`
    unchanged — this function mints nothing and makes no HTTP call of its
    own; that belongs to `service/market_terms.py`, reached through the cold
    path.
    """
    rows = await _read_book(session, market_id)
    if not rows:
        await books.ensure_open(
            session, market_id, access_token=access_token, transport=transport
        )
        rows = await _read_book(session, market_id)

    state_version = rows[0].state_version
    b = rows[0].liquidity_b
    q = [row.q for row in rows]

    return Snapshot(
        market_id=market_id,
        state_version=state_version,
        prices=_quantized_prices(rows, lmsr_prices(q, b)),
        occurred_at=rows[0].state_changed_at,
    )


async def _read_book(session: AsyncSession, market_id: uuid.UUID) -> Sequence[Row]:
    """`q`, `b`, `state_version` and `state_changed_at` in one statement,
    ordered by position. D-013, no lock (D-012, D-036) — this read decides no
    write."""
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


def _quantized_prices(rows: Sequence[Row], raw: list[Decimal]) -> list[OutcomeSnapshot]:
    """`raw` zipped back onto the ids and positions `_read_book` ordered.

    `ROUND_HALF_UP`, with no direction to favour: nobody is charged a price
    here, unlike `core/pricing.py::quantize_cost`'s directional rounding of a
    cost.
    """
    return [
        OutcomeSnapshot(
            outcome_id=row.outcome_id,
            position=row.position,
            price=price.quantize(QUANTUM, rounding=ROUND_HALF_UP),
        )
        for row, price in zip(rows, raw)
    ]
