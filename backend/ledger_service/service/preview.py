"""What a trade will cost, before anybody commits to it. [T-1] #21.

`quote()` is a read with one narrow exception: on a market nobody has touched
yet, it is the caller D-008 and D-037 were written for, and it opens and funds
that market's book before it can price anything. Every request after the first
is a single indexed read that writes nothing (D-036).

**The order, because the criteria pin it.**

1. `outcome_id`, `side` and `quantity` are already validated by the time this
   is called — the controller parses them off the query string before this
   function is reached, so nothing here touches the database or the network
   on a malformed request.
2. One statement joins `market_books` to `market_outcomes` (D-013), reading
   `q`, `b` and `state_version` together, with no lock (D-012, D-036).
3. If it returns nothing, `service.books.ensure_open` opens the book — which
   releases this read's transaction before it calls market_service (D-NEW,
   "The cold path holds no connection across the terms pull") and takes the
   handoff's own locks internally — and the read runs again. A second empty
   read is `MarketBookIncomplete` (500), not an `IndexError`.
4. `outcome_id` is checked against the book once it exists. A bad one is
   `UnknownOutcome` (422), and the book stays: it was real and published, and
   the write is a market's first touch either way (D-037's Notes).
5. A sell larger than that outcome's `q` is `InsufficientSharesOutstanding`
   (409) — the no-shorting rule against shares outstanding, not a per-user
   holding, which is [T-3] #23's under its own lock (D-012).
6. `core/lmsr.py::cost_to_trade` prices the trade. `core/pricing.py`
   quantizes the unsigned magnitude by side, and the sign is applied here:
   negative on a buy, positive on a sell. A resulting `q` or a magnitude
   above what `Numeric(18, 4)` can store is `QuantityTooLarge` (422) rather
   than a quote [T-2] #22 could not persist (D-040), and both are checked
   before anything is quantized. A total that quantizes to `0.0000` is
   refused by `quantize_cost` itself — `ProceedsBelowTick` on a sell,
   `CostBelowTick` on a buy — rather than quoted as real shares for nothing
   (D-041).
7. `average_price` is the quantized magnitude over `quantity`,
   `ROUND_HALF_UP` at scale 4 by `_quantize_price` — display, not money.
   Run inside `core/lmsr.py`'s pinned decimal context, the same one every
   other division in this service's pricing path uses, so an ambient trap or
   precision never reaches this one division.
8. `prices` and `post_trade_prices` are every outcome, `ROUND_HALF_UP` at
   scale 4 by the same `_quantize_price`, ordered by position.

Nothing here checks whether the market is still open. The book carries no
status and `close_time` is not snapshotted (ADR 0014 leaves it in the future
on an early close even), so there is nothing to check against — [T-2] #22
owns how the ledger eventually learns a market has stopped trading.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    InsufficientSharesOutstanding,
    MarketBookIncomplete,
    QuantityTooLarge,
    UnknownOutcome,
)
from core.lmsr import _engine_context, cost_to_trade, prices as lmsr_prices
from core.pricing import (
    MAX_MAGNITUDE,
    QUANTUM,
    Side,
    quantize_cost,
)
from model.entities import MarketBook, MarketOutcome
from service import books

ZERO = Decimal(0)


@dataclass(frozen=True)
class OutcomeQuote:
    """One outcome's price, in `PriceEvent`'s shape (`realtime_service`'s
    `OutcomePrice`): an id, its position, and a price quantized for the wire.
    """

    outcome_id: uuid.UUID
    position: int
    price: Decimal


@dataclass(frozen=True)
class Quote:
    """What one trade would cost, and how it would move the market."""

    market_id: uuid.UUID
    state_version: int
    side: Side
    outcome_id: uuid.UUID
    quantity: Decimal
    total: Decimal
    average_price: Decimal
    prices: list[OutcomeQuote]
    post_trade_prices: list[OutcomeQuote]


async def quote(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    outcome_id: uuid.UUID,
    side: Side | str,
    quantity: Decimal,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Quote:
    """Price one trade against this market's current state.

    `access_token` is the caller's own, forwarded to `books.ensure_open`
    unchanged — this function mints nothing and never sees a raw HTTP
    response itself; that belongs to `service/market_terms.py`, reached
    through the cold path.
    """
    side = Side(side)
    rows = await _read_book(session, market_id)
    if not rows:
        await books.ensure_open(
            session, market_id, access_token=access_token, transport=transport
        )
        rows = await _read_book(session, market_id)
        if not rows:
            # A book with no outcome rows reads as no book through the inner
            # join, so `ensure_open` found it and returned. Unreachable
            # through `ensure_open`, which writes both in one savepoint.
            raise MarketBookIncomplete

    state_version = rows[0].state_version
    b = rows[0].liquidity_b
    ids = [row.outcome_id for row in rows]
    q = [row.q for row in rows]

    if outcome_id not in ids:
        raise UnknownOutcome

    index = ids.index(outcome_id)

    if side is Side.SELL and quantity > q[index]:
        raise InsufficientSharesOutstanding

    delta = [ZERO] * len(q)
    delta[index] = quantity if side is Side.BUY else -quantity

    with _engine_context():
        after_q = [q_i + d_i for q_i, d_i in zip(q, delta)]

    # D-040, both halves: [T-2] #22 writes the cost *and* the resulting `q`
    # into `Numeric(18, 4)`, and a quote for either one it cannot store is a
    # quote it cannot honour. Checked before quantizing, because quantizing a
    # value wider than the ambient 28 digits raises `InvalidOperation`.
    if after_q[index] > MAX_MAGNITUDE:
        raise QuantityTooLarge

    # `copy_abs`, never `abs` (D-042): `abs` rounds at the ambient precision
    # and can carry the magnitude across a tick before the directional
    # rounding runs.
    raw = cost_to_trade(q, b, delta).copy_abs()
    if raw > MAX_MAGNITUDE:
        raise QuantityTooLarge

    magnitude = quantize_cost(raw, side=side)
    total = -magnitude if side is Side.BUY else magnitude
    with _engine_context():
        average_price = _quantize_price(magnitude / quantity)

    return Quote(
        market_id=market_id,
        state_version=state_version,
        side=side,
        outcome_id=outcome_id,
        quantity=quantity,
        total=total,
        average_price=average_price,
        prices=_quantized_prices(rows, lmsr_prices(q, b)),
        post_trade_prices=_quantized_prices(rows, lmsr_prices(after_q, b)),
    )


async def _read_book(session: AsyncSession, market_id: uuid.UUID) -> Sequence[Row]:
    """`q`, `b` and `state_version` in one statement, ordered by position.

    D-013: one snapshot under READ COMMITTED, so a trade committing between
    two separate reads can never hand back a `state_version` newer than the
    `q` this priced. No `with_for_update()` — D-012, as corrected by D-036:
    the pricing read decides no write, so it takes no lock, whatever this
    function returns.
    """
    stmt = (
        select(
            MarketBook.state_version,
            MarketBook.liquidity_b,
            MarketOutcome.outcome_id,
            MarketOutcome.position,
            MarketOutcome.q,
        )
        .join(MarketOutcome, MarketOutcome.market_id == MarketBook.market_id)
        .where(MarketBook.market_id == market_id)
        .order_by(MarketOutcome.position)
    )
    return (await session.execute(stmt)).all()


def _quantize_price(value: Decimal) -> Decimal:
    """A display figure — a price or `average_price` — at scale 4, half up.

    One definition for every figure this module shows and nobody is charged,
    so there is no side for the residue to favour. `total` never comes
    through here: it is money, and `core/pricing.py::quantize_cost` owns it.

    Shaped as a drop-in for #110's `core/pricing.py::quantize_price`, which
    replaces it: same argument, same rule, no context of its own.
    """
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _quantized_prices(rows: Sequence[Row], raw: list[Decimal]) -> list[OutcomeQuote]:
    """`raw` zipped back onto the ids and positions `_read_book` ordered."""
    return [
        OutcomeQuote(
            outcome_id=row.outcome_id,
            position=row.position,
            price=_quantize_price(price),
        )
        for row, price in zip(rows, raw)
    ]
