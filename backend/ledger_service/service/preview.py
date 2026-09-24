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
   `q`, `b` and `state_version` together, with no lock (D-012, D-036). It is
   `service/book_prices.py`'s, shared with the snapshot, as is step 3.
3. If it returns nothing, `service.books.ensure_open` opens the book — which
   releases this read's transaction before it calls market_service (D-043,
   "The cold path holds no connection across the terms pull") and takes the
   handoff's own locks internally — and the read runs again. A second empty
   read is `MarketBookIncomplete` (500), raised by `book_prices`.
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
7. `average_price` is the quantized magnitude over `quantity`, through
   `core/pricing.py::quantize_price` — display, not money. Run inside
   `core/lmsr.py`'s pinned decimal context, the same one every other division
   in this service's pricing path uses, so an ambient trap or precision never
   reaches this one division.
8. `prices` and `post_trade_prices` are every outcome, `ROUND_HALF_UP` at
   scale 4 by the same `_quantize_price`, ordered by position.

Nothing here checks whether the market is still open, and since [F-8] #109
that is a decision rather than an absence. The book carries no status and
`close_time` is not snapshotted (ADR 0014 leaves it in the future on an early
close even), so the answer can only come from market_service —
`service/market_status.py::ensure_trading` is the one place that asks, and
ADR 0017 keeps it off this path on purpose: a preview is arithmetic, it moves
no money, and putting an HTTP call on a route that fires on every keystroke
would buy nothing a closed market's refused trade does not already buy.
[T-2] #22 is the caller that gates.
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

    `access_token` is the caller's own, forwarded to `books.ensure_open`
    unchanged — this function mints nothing and never sees a raw HTTP
    response itself; that belongs to `service/market_terms.py`, reached
    through the cold path.
    """
    # Coerced before the `is` comparisons below. `Side` is a `StrEnum`, so
    # `"sell" is Side.SELL` is False and a raw string would fall through every
    # one of them as a buy.
    side = Side(side)
    rows = await book_prices.read_or_open(
        session, market_id, access_token=access_token, transport=transport
    )

    # `read_or_open` has already refused a book this service cannot price —
    # fewer rows than `MIN_OUTCOMES`, or a `b` the engine cannot use — so
    # everything below is arithmetic on a book known to be priceable. That
    # guard lives there rather than here because the snapshot reads through
    # the same function and needs the same answer.
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
    # `quantize_price`, not a third hand-rolled copy of it: an average
    # price is a price, and `core/pricing.py` owns that rounding rule for
    # every price this service publishes. The division stays inside the
    # engine's pinned context so an ambient trap or precision cannot reach
    # it; the rounding after it is the same half-up at scale 4 the
    # snapshot and the preview's own `prices` take.
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

