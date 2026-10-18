"""What a user's resting buy orders hold back. [T-6] #26, ADR 0024.

A reservation is derived from the order rows on every read and never stored,
for the reason a balance is not (ADR 0009). It is not an entry: the credits
stay in the balance, and `posting` keeps the balance at or above them.

Its own module because `posting` reads it, and the placement that will write
orders calls `posting`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import Side
from model.entities import MarketResult, Order, OrderStatus

ZERO = Decimal(0)


def reserved_query_of(user_id: uuid.UUID) -> Select[tuple[Decimal]]:
    """The credits this user's live buy orders could still spend: the sum of
    `budget - filled_value` over them, zero when there are none.

    Live is `open` with no `market_results` row for its market: D-NEW "An open
    order is live only while its market has no result". A filled order holds
    nothing, whatever its budget left over. A single-column SELECT, like
    `accounts.balance_query_of`, so the balance read can take it as a subquery
    of its own statement.
    """
    has_result = (
        select(MarketResult.market_id)
        .where(MarketResult.market_id == Order.market_id)
        .exists()
    )
    return select(func.coalesce(func.sum(Order.budget - Order.filled_value), ZERO)).where(
        Order.user_id == user_id,
        Order.side == Side.BUY,
        Order.status == OrderStatus.OPEN,
        ~has_result,
    )


async def reserved_of(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """What this user's live buy orders reserve, read now. Takes no lock: the
    overdraft check calls it holding the account's."""
    return (await session.execute(reserved_query_of(user_id))).scalar_one()
