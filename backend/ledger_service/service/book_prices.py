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

**A second read that cannot be priced is `MarketBookIncomplete`**, not an
`IndexError` and not a bare `ValueError` out of the engine. The join reads a
book with no outcome rows as no book, so the cold path finds the book,
returns, and the read comes back empty a second time. A book left holding a
single row is the same fault one step along: it survives an emptiness check
and dies in `core/lmsr.py`, so the floor here is `books.MIN_OUTCOMES`.
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
    if not rows:
        await books.ensure_open(
            session, market_id, access_token=access_token, transport=transport
        )
        rows = await _read(session, market_id)

    # Fewer than two, not zero, and on **both** paths. `core/lmsr.py` refuses
    # a `q` naming one outcome with a bare `ValueError`, which is not a
    # `LedgerError` and reaches the client as the same unmapped 500 this check
    # exists to stop. Guarding only the cold path would have missed the case
    # entirely: a book damaged down to one row already exists, so every read
    # of it is warm and returns before the cold path is reached. Same floor
    # `books._refuse_unpriceable` enforces on the way in.
    if len(rows) < books.MIN_OUTCOMES:
        raise MarketBookIncomplete

    # The book's own `b`, before it reaches the engine. `books.ensure_open`
    # refuses a non-finite or non-positive one at the ingress, which protects
    # every book written from now on and nothing already there — a row from
    # before that guard, or from the hand-run repair this error exists to
    # name, still prices. `core/lmsr.py::_require_positive_b` then raises a
    # bare `ValueError`, which is not a `LedgerError`, so it reaches the
    # caller as an unmapped 500 on an immutable book, forever.
    #
    # `NaN` is why this is worth having rather than a null check: it is the
    # only unpriceable `b` `numeric(18, 4)` will store, and it does not even
    # reach `_require_positive_b` as False — `NaN <= 0` raises.
    b = rows[0].liquidity_b
    if not b.is_finite() or b <= 0:
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
        # `strict`: a `raw` shorter than `rows` is a caller passing the
        # wrong price vector, and silently returning a market missing an
        # outcome renders prices that no longer sum to one.
        for row, price in zip(rows, raw, strict=True)
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
