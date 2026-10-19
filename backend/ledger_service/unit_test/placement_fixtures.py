"""Fixtures the [T-6] #26 placement tests share.

Built on `trade_fixtures.py`, as `sell_fixtures.py` is: the same `Upstream`
and `Recorder`, and `place` beside `buy` with the same seams. What is added is
a market at `b = 100`, the contract's own worked example in
`docs/api/ledger-service.md`, and the reads a placement needs that a trade
did not: the order rows and what they reserve.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import Side
from model.entities import MarketResult, Order, ResultKind
from service import books, orders, reservations
from unit_test.conftest import (
    active_auth_transport,
    auth_client_over,
    mint_token,
    set_q,
    terms_client_over,
)
from unit_test.trade_fixtures import Recorder, Upstream

# The contract's example market: `b = 100` at `q = [20, 0]`, where outcome 0
# is priced 0.5498. A buy of outcome 0 at 0.6000 fills 20.5465 shares at once
# for 11.8152 and takes the price to its limit.
LIMIT_B = Decimal("100.0000")
EXAMPLE_Q = (Decimal("20.0000"), Decimal("0.0000"))


def limit_upstream(*, outcomes: int = 2) -> Upstream:
    """A market service stub whose market has `b = 100`."""
    upstream = Upstream(outcomes=outcomes)
    upstream.body["liquidity_b"] = str(LIMIT_B)
    return upstream


async def open_at(
    session: AsyncSession, upstream: Upstream, q: Sequence[Decimal] = EXAMPLE_Q
) -> None:
    """Open and fund the book, then write `q`. Committed. The upstream's
    counters are reset, so a test counts only the placement's own hops."""
    await books.ensure_open(
        session,
        upstream.market_id,
        access_token=mint_token(uuid.uuid4()),
        terms_client=terms_client_over(upstream.transport),
    )
    await set_q(session, upstream.market_id, q)
    upstream.calls = 0
    upstream.tokens.clear()


async def place(
    session: AsyncSession,
    upstream: Upstream,
    *,
    user_id: uuid.UUID,
    limit_price: Decimal,
    quantity: Decimal,
    outcome: int = 0,
    outcome_id: uuid.UUID | None = None,
    side: str = "buy",
    client_key: str | None = None,
    access_token: str | None = None,
    redis_client: Recorder | None = None,
    auth_transport: httpx.MockTransport | None = None,
):
    """One placement through `orders.place`, shaped like `trade_fixtures.buy`:
    session and market id positionally, the rest keyword-only, the caller's
    token forwarded, and the gate, auth and Redis as seams. `outcome_id`
    overrides `outcome` for a test that names another market's outcome."""
    return await orders.place(
        session,
        upstream.market_id,
        user_id=user_id,
        outcome_id=outcome_id if outcome_id is not None else upstream.outcomes[outcome],
        side=Side(side),
        limit_price=limit_price,
        quantity=quantity,
        idempotency_key=client_key or f"order-{uuid.uuid4()}",
        access_token=access_token if access_token is not None else mint_token(user_id),
        redis_client=redis_client if redis_client is not None else Recorder(),
        terms_client=terms_client_over(upstream.transport),
        auth_client=auth_client_over(
            active_auth_transport() if auth_transport is None else auth_transport
        ),
    )


async def order_count(session: AsyncSession) -> int:
    session.expire_all()
    return (
        await session.execute(select(func.count()).select_from(Order))
    ).scalar_one()


async def order_row(session: AsyncSession, order_id: uuid.UUID):
    session.expire_all()
    return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()


async def reserved_credits_of(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    session.expire_all()
    return await reservations.reserved_of(session, user_id)


async def record_result(session: AsyncSession, upstream: Upstream) -> None:
    """Write the market's `market_results` row, as a settlement does. Committed.

    Only the row: what a settlement pays is not what these tests are about,
    and the row alone is what makes a market's orders stop being live.
    """
    session.add(
        MarketResult(
            market_id=upstream.market_id,
            kind=ResultKind.SETTLED,
            outcome_id=upstream.outcomes[0],
            recorded_at=datetime.now(UTC),
        )
    )
    await session.commit()
