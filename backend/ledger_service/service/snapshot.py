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

The read, the cold path and the quantizer are `service/book_prices.py`'s,
shared with the preview: one joined statement (D-013), no lock (D-012,
D-036), and `books.ensure_open` taking the handoff's own locks on the cold
path. Shared rather than copied, because both docs pages promise the two
endpoints the same price strings for the same state.
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
    transport: httpx.AsyncBaseTransport | None = None,
) -> Snapshot:
    """This market's current price. Opens the book first if nobody has yet.

    `access_token` is the caller's own, forwarded to `books.ensure_open`
    unchanged — this function mints nothing and makes no HTTP call of its
    own; that belongs to `service/market_terms.py`, reached through the cold
    path.
    """
    rows = await book_prices.read_or_open(
        session, market_id, access_token=access_token, transport=transport
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

