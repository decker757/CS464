"""What a user's resting orders hold back. [T-6] #26, ADR 0024.

A reservation is derived from the order rows on every read and never stored,
for the reason a balance is not (ADR 0009). It is not an entry: the credits
stay in the balance, and `posting` keeps the balance at or above them; the
shares stay in the position, and the sell paths keep a sell within the rest.

Its own module because `posting` reads it, and the placement, which writes
orders, calls `posting`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import Side
from model.entities import MarketResult, Order, OrderStatus

ZERO = Decimal(0)


def order_is_live() -> ColumnElement[bool]:
    """The condition, for a WHERE clause or a selected column, that an order
    is live: `open`, with no `market_results` row for its market.

    D-NEW "An open order is live only while its market has no result". Every
    read of what an order holds asks this, so a status column that reads
    `open` for ever on a finished market is never the authority.
    """
    has_result = (
        select(MarketResult.market_id)
        .where(MarketResult.market_id == Order.market_id)
        .exists()
    )
    return and_(Order.status == OrderStatus.OPEN, ~has_result)


def reserved_query_of(user_id: uuid.UUID) -> Select[tuple[Decimal]]:
    """The credits this user's live buy orders could still spend: the sum of
    `budget - filled_value` over them, zero when there are none.

    A filled order holds nothing, whatever its budget left over. A
    single-column SELECT, like `accounts.balance_query_of`, so the balance
    read can take it as a subquery of its own statement.
    """
    return select(func.coalesce(func.sum(Order.budget - Order.filled_value), ZERO)).where(
        Order.user_id == user_id,
        Order.side == Side.BUY,
        order_is_live(),
    )


async def reserved_of(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """What this user's live buy orders reserve, read now. Takes no lock: the
    overdraft check calls it holding the account's."""
    return (await session.execute(reserved_query_of(user_id))).scalar_one()


def reserved_shares_query_of(
    user_id: uuid.UUID,
    *,
    market_id: uuid.UUID | ColumnElement[uuid.UUID],
    outcome_id: uuid.UUID | ColumnElement[uuid.UUID],
) -> Select[tuple[Decimal]]:
    """The shares of one outcome this user's live sell orders still hold back:
    the sum of `quantity - filled_quantity` over them, zero when there are none.

    The market and outcome are values, or columns of an enclosing statement
    that takes this as a correlated subquery.
    """
    return select(
        func.coalesce(func.sum(Order.quantity - Order.filled_quantity), ZERO)
    ).where(
        Order.user_id == user_id,
        Order.market_id == market_id,
        Order.outcome_id == outcome_id,
        Order.side == Side.SELL,
        order_is_live(),
    )


async def reserved_shares_of(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
) -> Decimal:
    """What this user's live sell orders of one outcome reserve, read now.

    Takes no lock: every caller holds the market's book row, which every
    writer of an order or a position takes first.
    """
    stmt = reserved_shares_query_of(user_id, market_id=market_id, outcome_id=outcome_id)
    return (await session.execute(stmt)).scalar_one()
