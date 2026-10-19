"""A limit order row, for the tests that need a reservation to exist.

`test_reservations.py`, `test_overdraft_floor.py`, `test_trade.py` and
`test_ledger_routes.py` each need an order whose reservation is a known
figure, and none of them is testing how an order is placed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import Side
from model.entities import Order, OrderStatus


def _rollups_of(
    status: OrderStatus, *, filled_value: str, limit_price: str
) -> tuple[Decimal, Decimal]:
    """This order's `(filled_quantity, filled_value)`, which move together.

    A filled order has its one share filled, and spent at the limit when the
    caller named no value: `ck_orders_filled_quantity_iff_filled_value`
    refuses a share filled for nothing. An open order has half a share filled
    when anything has filled, and nothing filled otherwise.
    """
    value = Decimal(filled_value)
    if status is OrderStatus.FILLED:
        return Decimal("1.0000"), value if value > 0 else Decimal(limit_price)
    if value > 0:
        return Decimal("0.5000"), value
    return Decimal("0.0000"), value


async def add_order(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
    budget: str | None,
    filled_value: str = "0.0000",
    status: str = "open",
    limit_price: str = "0.5000",
) -> Order:
    """One order of a single share at 0.5000, flushed, and returned.

    A buy when `budget` is given, a sell when it is None. A single share at
    0.5000 lets any budget from 0.5000 up pass
    `ck_orders_budget_covers_the_limit`, so a test chooses the reservation,
    `budget - filled_value`, directly. The fills are `_rollups_of`'s and a
    cancelled order gets a cancel time, so every CHECK on the table holds.

    `limit_price` is for a test that trades in the order's own market: an
    order the price has passed would fill in that trade's cascade.
    """
    order_status = OrderStatus(status)
    filled_quantity, value_filled = _rollups_of(
        order_status, filled_value=filled_value, limit_price=limit_price
    )

    order = Order(
        user_id=user_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=Side.SELL if budget is None else Side.BUY,
        limit_price=Decimal(limit_price),
        quantity=Decimal("1.0000"),
        filled_quantity=filled_quantity,
        budget=None if budget is None else Decimal(budget),
        filled_value=value_filled,
        status=order_status,
        book_state_version=0,
        idempotency_key=f"order:{uuid.uuid4()}",
        cancelled_at=(
            datetime.now(UTC) if order_status is OrderStatus.CANCELLED else None
        ),
    )
    session.add(order)
    await session.flush()
    return order
