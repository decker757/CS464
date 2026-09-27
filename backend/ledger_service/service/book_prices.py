"""The price read the preview and the snapshot share. D-052.

One copy, so both endpoints return the same price strings for the same state.
One statement joins `market_books` to `market_outcomes`, so `q`, `b` and
`state_version` come from one snapshot (D-013). No lock: it decides no write
(D-012, D-036). On an empty read `books.ensure_open` opens the book and the
read runs again (D-037).
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
    book first if nobody has yet, forwarding `access_token` unchanged.

    Every row carries `state_version`, `liquidity_b`, `state_changed_at`,
    `outcome_id`, `position` and `q`. Raises `MarketBookIncomplete` rather
    than return a book the engine cannot price.
    """
    rows = await _read(session, market_id)
    if not rows:
        await books.ensure_open(
            session, market_id, access_token=access_token, transport=transport
        )
        rows = await _read(session, market_id)

    # On both paths: a book damaged down to one row is warm and never
    # reaches the cold path.
    refuse_unpriceable(len(rows), rows[0].liquidity_b if rows else None)

    return rows


def refuse_unpriceable(outcome_count: int, b: Decimal | None) -> None:
    """Raise `MarketBookIncomplete` for a book the engine cannot price.

    The one copy of this guard: the trade path's locked read calls it too, so
    the trade and the preview refuse the same books. Takes a count and `b`
    because the two callers read different shapes.
    """
    # Fewer than two, not zero: `core/lmsr.py` raises a bare `ValueError` on
    # one outcome, an unmapped 500.
    if outcome_count < books.MIN_OUTCOMES:
        raise MarketBookIncomplete

    # `books.ensure_open` guards new books, not rows already stored. NaN is
    # the one unpriceable `b` `numeric` will store, and `NaN <= 0` raises, so
    # finiteness is checked first.
    if b is None or not b.is_finite() or b <= 0:
        raise MarketBookIncomplete


def priced(rows: Sequence[Row], raw: list[Decimal]) -> list[PricedOutcome]:
    """Zips `raw` back onto the ids and positions `read_or_open` ordered,
    each quantized by `core/pricing.py::quantize_price`."""
    return [
        PricedOutcome(
            outcome_id=row.outcome_id,
            position=row.position,
            price=quantize_price(price),
        )
        # `strict`: a `raw` of the wrong length is a caller bug, and a
        # silently dropped outcome renders prices that do not sum to one.
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
