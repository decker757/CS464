"""What a trade will cost, before anybody commits to it. [T-1] #21.

A read, except that on a market nobody has touched it opens and funds the book
first (D-037). After that it is one unlocked read (D-013, D-036), shared with
the snapshot through `service/book_prices.py`.

It never asks whether the market is open: a preview moves no money and fires
on every keystroke, so the gate is the trade's alone (ADR 0017).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    InsufficientSharesOutstanding,
    QuantityTooLarge,
    UnknownOutcome,
)
from core.lmsr import _engine_context, cost_to_trade, prices as lmsr_prices
from core.pricing import (
    MAX_MAGNITUDE,
    Side,
    quantize_cost,
    quantize_price,
)
from service import book_prices
from service.book_prices import PricedOutcome

ZERO = Decimal(0)


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
    prices: list[PricedOutcome]
    post_trade_prices: list[PricedOutcome]


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

    Raises `UnknownOutcome`; `InsufficientSharesOutstanding` for a sell above
    the outcome's `q` (the caller's own holding is the trade's check); and
    `QuantityTooLarge` for a cost or `q` beyond `Numeric(18, 4)` (D-040).
    `quantize_cost` refuses a total that quantizes to zero (D-041). On a cold
    market the first touch opens and funds the book and commits, forwarding
    `access_token` unchanged, so call it with nothing pending on the session.
    """
    # Coerced before the `is` comparisons below: `"sell" is Side.SELL` is
    # False, so a raw string would fall through every one of them as a buy.
    side = Side(side)
    rows = await book_prices.read_or_open(
        session, market_id, access_token=access_token, transport=transport
    )

    # `read_or_open` has refused an unpriceable book, so this is arithmetic
    # on a priceable one.
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

    # D-040: the trade stores both the cost and the new `q` in
    # `Numeric(18, 4)`. Checked before quantizing, which raises
    # `InvalidOperation` past the ambient 28 digits.
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
    # An average price is a price, so `quantize_price` rounds it (D-052). The
    # division runs in the engine's pinned context, out of reach of an
    # ambient trap or precision.
    with _engine_context():
        average_price = quantize_price(magnitude / quantity)

    return Quote(
        market_id=market_id,
        state_version=state_version,
        side=side,
        outcome_id=outcome_id,
        quantity=quantity,
        total=total,
        average_price=average_price,
        prices=book_prices.priced(rows, lmsr_prices(q, b)),
        post_trade_prices=book_prices.priced(rows, lmsr_prices(after_q, b)),
    )

